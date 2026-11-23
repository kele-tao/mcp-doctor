"""The MCP client: handshake, request/response correlation and pagination.

Peer is the object every command shares. It owns exactly one transport, keeps
every frame it sent and received (so the HTML report can quote real evidence),
and turns protocol-level surprises into typed exceptions instead of hangs.

Protocol note: MCP pins a protocol version per session. The client proposes the
newest version it knows; the server answers with the version it supports, and
if that is a version this client does not know we still keep talking, because
the point of mcp-doctor is to inspect the server, not to police it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import jsonrpc
from .errors import ServerError, TransportError
from .transports import DEFAULT_TIMEOUT, Transport, build_transport
from .version import __version__

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
CLIENT_INFO = {"name": "mcp-doctor", "version": __version__}


@dataclass
class Exchange:
    """One request and the response that answered it."""

    request: dict
    response: dict | None
    elapsed_ms: float
    error: str | None = None

    def summary(self) -> str:
        """One-line transcript entry, used by verbose output and the HTML report."""
        method = self.request.get("method", "?")
        if self.response is None:
            return f"{method} -> {self.error or 'no response'}"
        if "error" in self.response:
            error = self.response["error"]
            return f"{method} -> error {error.get('code')} ({error.get('message')})"
        return f"{method} -> ok in {self.elapsed_ms:.1f} ms"


@dataclass
class ServerInfo:
    """The useful half of the initialize result."""

    protocol_version: str
    server_name: str
    server_version: str
    capabilities: dict = field(default_factory=dict)
    instructions: str | None = None
    raw: dict = field(default_factory=dict)


class Peer:
    """A connected MCP peer with JSON-RPC request/response correlation."""

    def __init__(self, transport: Transport, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.transport = transport
        self.timeout = timeout
        self.server_info: ServerInfo | None = None
        self.exchanges: list[Exchange] = []
        self.notifications: list[dict] = []
        self.late_responses: list[dict] = []
        self.warnings: list[str] = []
        self._next_id = 1
        self._closed = False

    # -- low level -------------------------------------------------------- #

    def send(self, message: dict) -> None:
        """Send one JSON-RPC message."""
        self.transport.send_raw(jsonrpc.dumps(message))

    def receive(self, timeout: float | None = None) -> dict:
        """Receive the next JSON-RPC message.

        The MCP spec allows a server to emit notifications at any time, so we
        stash them instead of failing the request that is waiting.
        """
        raw = self.transport.receive(self.timeout if timeout is None else timeout)
        try:
            return jsonrpc.parse(raw)
        except jsonrpc.JsonRpcError as exc:
            raise TransportError(
                f"server sent a malformed JSON-RPC frame ({exc.message}): {raw[:200]}"
            ) from exc

    def notify(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification."""
        self.send(jsonrpc.make_notification(method, params))

    def request(
        self,
        method: str,
        params: dict | None = None,
        timeout: float | None = None,
        expect_error: bool = False,
    ) -> dict:
        """Send a request and wait for its response.

        Notifications that arrive while waiting are collected in
        self.notifications. The exchange, including failures, is appended to
        self.exchanges so reports can quote real evidence.
        """
        if self._closed:
            raise TransportError("peer is closed")
        request_id = self._next_id
        self._next_id += 1
        message = jsonrpc.make_request(request_id, method, params)
        started = time.perf_counter()
        self.send(message)
        while True:
            incoming = self.receive(timeout)
            if jsonrpc.is_response(incoming):
                elapsed = (time.perf_counter() - started) * 1000.0
                if incoming.get("id") != request_id:
                    self.late_responses.append(incoming)
                    self.warnings.append(
                        "response id "
                        + repr(incoming.get("id"))
                        + " arrived while waiting for request id "
                        + repr(request_id)
                        + " ("
                        + method
                        + ")"
                    )
                    continue
                self.exchanges.append(Exchange(message, incoming, round(elapsed, 2)))
                if "error" in incoming:
                    if expect_error:
                        return incoming
                    error = incoming["error"]
                    raise ServerError(
                        int(error.get("code", 0)),
                        str(error.get("message", "unknown error")),
                        error.get("data"),
                    )
                return incoming
            if jsonrpc.is_request(incoming):
                self.warnings.append(
                    f"server sent an unsolicited request {incoming.get('method')!r} "
                    "that mcp-doctor answered with method-not-found"
                )
                self.send(
                    jsonrpc.make_error(
                        incoming.get("id"),
                        jsonrpc.METHOD_NOT_FOUND,
                        f"client does not implement {incoming.get('method')!r}",
                    )
                )
                continue
            self.notifications.append(incoming)

    def try_request(self, method: str, params: dict | None = None) -> dict:
        """Like request() but returns the raw response object, error or not."""
        return self.request(method, params, expect_error=True)

    # -- handshake -------------------------------------------------------- #

    def initialize(
        self, protocol_version: str = LATEST_PROTOCOL_VERSION, send_initialized: bool = True
    ) -> dict:
        """Run the initialize handshake and remember the negotiated version."""
        response = self.request(
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": dict(CLIENT_INFO),
            },
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise TransportError("initialize result is not an object")
        version = result.get("protocolVersion")
        if not isinstance(version, str):
            raise TransportError("initialize result is missing protocolVersion")
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            self.warnings.append(
                f"server negotiated protocol {version!r}, which this build does not know; "
                "checks that need newer semantics may be skipped"
            )
        server = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
        self.server_info = ServerInfo(
            protocol_version=version,
            server_name=str(server.get("name", "")),
            server_version=str(server.get("version", "")),
            capabilities=result.get("capabilities") or {},
            instructions=result.get("instructions"),
            raw=result,
        )
        if send_initialized:
            self.notify("notifications/initialized")
        return result

    # -- convenience wrappers --------------------------------------------- #

    def ping(self) -> dict:
        """Send an MCP ping and return the raw response."""
        return self.try_request("ping")

    def list_tools(self) -> tuple[list[dict], list[str]]:
        """List every tool, following pagination cursors."""
        return self._paginate("tools/list", "tools")

    def list_resources(self) -> tuple[list[dict], list[str]]:
        """List every resource, following pagination cursors."""
        return self._paginate("resources/list", "resources")

    def list_prompts(self) -> tuple[list[dict], list[str]]:
        """List every prompt, following pagination cursors."""
        return self._paginate("prompts/list", "prompts")

    def list_resource_templates(self) -> tuple[list[dict], list[str]]:
        """List resource templates, following pagination cursors."""
        return self._paginate("resources/templates/list", "resourceTemplates")

    def _paginate(self, method: str, key: str) -> tuple[list[dict], list[str]]:
        items: list[dict] = []
        errors: list[str] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(100):
            params = {"cursor": cursor} if cursor else None
            response = self.try_request(method, params)
            if "error" in response:
                error = response["error"]
                errors.append(str(error.get("message", "unknown error")))
                break
            result = response.get("result")
            if not isinstance(result, dict):
                errors.append(f"{method} returned a non-object result")
                break
            page = result.get(key, [])
            if isinstance(page, list):
                items.extend(item for item in page if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not cursor or not isinstance(cursor, str):
                break
            if cursor in seen_cursors:
                errors.append(f"{method} repeated cursor {cursor!r}: pagination does not advance")
                break
            seen_cursors.add(cursor)
        return items, errors

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call a tool and return the tools/call result object."""
        response = self.try_request("tools/call", {"name": name, "arguments": arguments or {}})
        if "error" in response:
            raise ServerError(
                int(response["error"].get("code", 0)),
                str(response["error"].get("message", "unknown error")),
                response["error"].get("data"),
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise TransportError("tools/call result is not an object")
        return result

    def read_resource(self, uri: str) -> dict:
        """Read a resource by URI."""
        response = self.try_request("resources/read", {"uri": uri})
        if "error" in response:
            raise ServerError(
                int(response["error"].get("code", 0)),
                str(response["error"].get("message", "unknown error")),
                response["error"].get("data"),
            )
        return response.get("result") or {}

    def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        """Fetch a prompt definition."""
        params: dict[str, Any] = {"name": name}
        if arguments:
            params["arguments"] = arguments
        response = self.try_request("prompts/get", params)
        if "error" in response:
            raise ServerError(
                int(response["error"].get("code", 0)),
                str(response["error"].get("message", "unknown error")),
                response["error"].get("data"),
            )
        return response.get("result") or {}

    # -- lifecycle -------------------------------------------------------- #

    def close(self) -> None:
        """Close the transport exactly once."""
        if self._closed:
            return
        self._closed = True
        try:
            self.transport.close()
        except Exception:  # noqa: BLE001 - closing must never mask the real error
            pass

    def __enter__(self) -> Peer:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def describe(self) -> str:
        """Human label for headers and reports."""
        info = self.server_info
        if info and info.server_name:
            return f"{info.server_name} {info.server_version}".strip()
        return self.transport.describe()


def connect_stdio(command: str, timeout: float = DEFAULT_TIMEOUT, initialize: bool = True) -> Peer:
    """Spawn a stdio MCP server and complete the handshake."""
    peer = Peer(build_transport(command, None), timeout=timeout)
    if initialize:
        try:
            peer.initialize()
        except Exception:
            peer.close()
            raise
    return peer


def connect_http(url: str, timeout: float = DEFAULT_TIMEOUT, initialize: bool = True) -> Peer:
    """Connect to a Streamable HTTP MCP endpoint and complete the handshake."""
    peer = Peer(build_transport(None, url, timeout=timeout), timeout=timeout)
    if initialize:
        try:
            peer.initialize()
        except Exception:
            peer.close()
            raise
    return peer
