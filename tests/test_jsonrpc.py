"""JSON-RPC 2.0 envelope tests: the wire format is the contract."""

from __future__ import annotations

import pytest

from mcp_doctor import jsonrpc


def test_request_round_trip():
    message = jsonrpc.make_request(7, "tools/list", {"cursor": "abc"})
    parsed = jsonrpc.parse(jsonrpc.dumps(message))
    assert parsed == message
    assert jsonrpc.is_request(parsed)
    assert not jsonrpc.is_notification(parsed)


def test_notification_has_no_id():
    message = jsonrpc.make_notification("notifications/initialized")
    assert "id" not in message
    assert jsonrpc.is_notification(message)
    assert jsonrpc.is_request(message) is False


def test_result_and_error_envelopes():
    assert jsonrpc.is_response(jsonrpc.make_result(1, {}))
    assert jsonrpc.is_response(jsonrpc.make_error(1, -32601, "nope"))
    error = jsonrpc.make_error(1, -32601, "nope", {"hint": "x"})
    assert error["error"]["data"] == {"hint": "x"}
    assert jsonrpc.make_error(1, -32601, "nope")["error"] == {
        "code": -32601,
        "message": "nope",
    }


def test_error_code_names_cover_the_spec():
    assert jsonrpc.error_code_name(-32700) == "Parse error"
    assert jsonrpc.error_code_name(-32600) == "Invalid Request"
    assert jsonrpc.error_code_name(-32601) == "Method not found"
    assert jsonrpc.error_code_name(-32602) == "Invalid params"
    assert jsonrpc.error_code_name(-32000) == "Server error"


@pytest.mark.parametrize(
    "text",
    ["", "   ", "{not json", '{"jsonrpc": "2.0", "method": '],
)
def test_parse_error_for_broken_json(text):
    with pytest.raises(jsonrpc.JsonRpcError) as excinfo:
        jsonrpc.parse(text)
    assert excinfo.value.code == jsonrpc.PARSE_ERROR


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 1, "method": "ping"},
        {"jsonrpc": "1.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 1, "method": ""},
        {"jsonrpc": "2.0", "id": True, "method": "ping"},
        {"jsonrpc": "2.0", "result": {}},
        {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {"code": 0, "message": "x"}},
        {"jsonrpc": "2.0", "id": 1, "error": {"code": "0", "message": "x"}},
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "result": {}},
    ],
)
def test_invalid_envelopes_are_rejected(payload):
    with pytest.raises(jsonrpc.JsonRpcError) as excinfo:
        jsonrpc.validate_envelope(payload)
    assert excinfo.value.code == jsonrpc.INVALID_REQUEST


def test_batches_are_rejected_by_name():
    with pytest.raises(jsonrpc.JsonRpcError) as excinfo:
        jsonrpc.parse("[1, 2]")
    assert excinfo.value.code == jsonrpc.INVALID_REQUEST
    assert "batch" in excinfo.value.message


def test_describe_is_one_line():
    assert jsonrpc.describe(jsonrpc.make_request(1, "ping")).startswith("request ping")
    assert "error -32601" in jsonrpc.describe(jsonrpc.make_error(1, -32601, "no"))
    assert jsonrpc.describe(jsonrpc.make_notification("x")).startswith("notification")


def test_unicode_survives_serialisation():
    message = jsonrpc.make_result(1, {"text": "caf\u00e9 \u4f60\u597d \U0001f600"})
    assert (
        jsonrpc.parse(jsonrpc.dumps(message))["result"]["text"]
        == "caf\u00e9 \u4f60\u597d \U0001f600"
    )
