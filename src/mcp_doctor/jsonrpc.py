"""JSON-RPC 2.0 envelopes, error codes and strict parsing.

MCP is JSON-RPC 2.0 with newline framing over stdio (or HTTP bodies for the
Streamable HTTP transport), so getting the envelope right is half of protocol
conformance. Everything here is pure data in, pure data out: no sockets, no
subprocesses, which makes it trivially testable.
"""

from __future__ import annotations

import json
from typing import Any

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

ERROR_NAMES: dict[int, str] = {
    PARSE_ERROR: "Parse error",
    INVALID_REQUEST: "Invalid Request",
    METHOD_NOT_FOUND: "Method not found",
    INVALID_PARAMS: "Invalid params",
    INTERNAL_ERROR: "Internal error",
}


def error_code_name(code: int) -> str:
    """Return the JSON-RPC spec name for an error code."""
    return ERROR_NAMES.get(code, "Server error")


class JsonRpcError(Exception):
    """A malformed message that we refuse to send or could not parse."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def make_request(request_id: int | str, method: str, params: dict | None = None) -> dict:
    """Build a JSON-RPC 2.0 request object."""
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def make_notification(method: str, params: dict | None = None) -> dict:
    """Build a JSON-RPC 2.0 notification: no id, no response expected."""
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def make_result(request_id: int | str | None, result: Any) -> dict:
    """Build a JSON-RPC 2.0 success response."""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def make_error(request_id: int | str | None, code: int, message: str, data: Any = None) -> dict:
    """Build a JSON-RPC 2.0 error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def dumps(message: dict) -> str:
    """Serialise deterministically: compact separators, no trailing newline."""
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def parse(text: str) -> dict:
    """Parse one JSON-RPC message, raising JsonRpcError on any defect.

    Code -32700 is reserved for text that is not JSON at all; -32600 covers
    JSON that is not a valid JSON-RPC 2.0 envelope.
    """
    stripped = text.strip()
    if not stripped:
        raise JsonRpcError(PARSE_ERROR, "empty frame")
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise JsonRpcError(PARSE_ERROR, f"invalid JSON: {exc.msg} at char {exc.pos}") from exc
    if isinstance(value, list):
        raise JsonRpcError(INVALID_REQUEST, "JSON-RPC batches are not part of MCP")
    if not isinstance(value, dict):
        raise JsonRpcError(INVALID_REQUEST, "a JSON-RPC message must be an object")
    validate_envelope(value)
    return value


def validate_envelope(message: dict) -> None:
    """Raise JsonRpcError unless the message is a valid JSON-RPC 2.0 envelope."""
    if message.get("jsonrpc") != "2.0":
        raise JsonRpcError(INVALID_REQUEST, 'missing or incorrect "jsonrpc": "2.0"')
    has_method = "method" in message
    has_result = "result" in message
    has_error = "error" in message
    if has_method:
        if not isinstance(message["method"], str) or not message["method"]:
            raise JsonRpcError(INVALID_REQUEST, '"method" must be a non-empty string')
        if "id" in message and message["id"] is not None:
            if isinstance(message["id"], bool) or not isinstance(message["id"], (str, int)):
                raise JsonRpcError(INVALID_REQUEST, '"id" must be a string, a number or null')
        if has_result or has_error:
            raise JsonRpcError(
                INVALID_REQUEST, 'a request cannot carry both "method" and "result"/"error"'
            )
        if "params" in message and not isinstance(message["params"], (dict, list)):
            raise JsonRpcError(INVALID_REQUEST, '"params" must be an object or an array')
        return
    if has_result and has_error:
        raise JsonRpcError(INVALID_REQUEST, 'a response cannot carry both "result" and "error"')
    if not has_result and not has_error:
        raise JsonRpcError(INVALID_REQUEST, 'message has neither "method" nor "result"/"error"')
    if "id" not in message:
        raise JsonRpcError(INVALID_REQUEST, 'a response must carry an "id"')
    if has_error:
        error = message["error"]
        if not isinstance(error, dict):
            raise JsonRpcError(INVALID_REQUEST, '"error" must be an object')
        if isinstance(error.get("code"), bool) or not isinstance(error.get("code"), int):
            raise JsonRpcError(INVALID_REQUEST, '"error.code" must be an integer')
        if not isinstance(error.get("message"), str):
            raise JsonRpcError(INVALID_REQUEST, '"error.message" must be a string')


def is_request(message: dict) -> bool:
    """True when the message is a request: has both id and method."""
    return "method" in message and "id" in message


def is_notification(message: dict) -> bool:
    """True when the message is a notification: has method and no id."""
    return "method" in message and "id" not in message


def is_response(message: dict) -> bool:
    """True when the message is a response: has result or error."""
    return "result" in message or "error" in message


def describe(message: dict) -> str:
    """One-line human description used as evidence in reports."""
    if is_response(message):
        if "error" in message:
            error = message["error"]
            return f"error {error.get('code')} {error.get('message')!r}"
        return "result " + json.dumps(message.get("result"), ensure_ascii=False)[:120]
    method = message.get("method", "?")
    kind = "request" if is_request(message) else "notification"
    return f"{kind} {method}"
