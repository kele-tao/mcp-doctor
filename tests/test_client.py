"""Client tests against the real bundled demo server."""

from __future__ import annotations

import pytest

from mcp_doctor.client import (
    LATEST_PROTOCOL_VERSION,
    connect_stdio,
)
from mcp_doctor.errors import ServerError, TransportError


@pytest.fixture()
def peer(demo_command):
    connection = connect_stdio(demo_command)
    yield connection
    connection.close()


def test_initialize_records_server_info(peer):
    info = peer.server_info
    assert info is not None
    assert info.server_name == "demo-tools"
    assert info.protocol_version == LATEST_PROTOCOL_VERSION
    assert "tools" in info.capabilities
    assert info.instructions


def test_ping_and_notifications(peer):
    response = peer.ping()
    assert response["result"] == {}
    methods = [exchange.request.get("method") for exchange in peer.exchanges]
    assert "initialize" in methods
    assert "ping" in methods


def test_list_tools_and_resources(peer):
    tools, errors = peer.list_tools()
    assert [tool["name"] for tool in tools] == ["add", "echo", "word_count"]
    assert errors == []
    resources, errors = peer.list_resources()
    assert resources[0]["uri"] == "memo://handbook"
    assert errors == []


def test_unknown_capability_list_returns_error(peer):
    prompts, errors = peer.list_prompts()
    assert prompts == []
    assert errors and "not found" in errors[0]


def test_call_tool_success(peer):
    result = peer.call_tool("add", {"a": 2, "b": 40})
    assert result["structuredContent"] == {"sum": 42}
    assert result["content"][0]["text"] == "42"


def test_call_tool_server_error(peer):
    with pytest.raises(ServerError) as excinfo:
        peer.call_tool("add", {"a": 1})
    assert excinfo.value.code == -32602
    assert "missing arguments" in excinfo.value.message


def test_read_resource(peer):
    result = peer.read_resource("memo://handbook")
    assert "Demo handbook" in result["contents"][0]["text"]


def test_read_resource_error(peer):
    with pytest.raises(ServerError):
        peer.read_resource("memo://missing")


def test_peer_closes_once(peer):
    peer.close()
    peer.close()
    with pytest.raises(TransportError):
        peer.request("ping")


def test_connect_stdio_bad_command():
    with pytest.raises(TransportError):
        connect_stdio("definitely-not-a-real-binary-zzz")


def test_peer_avoids_infinite_pagination(monkeypatch, peer):
    calls = {"n": 0}

    def fake_request(method, params=None, timeout=None, expect_error=False):
        calls["n"] += 1
        return {"jsonrpc": "2.0", "id": 1, "result": {"tools": [], "nextCursor": "same"}}

    monkeypatch.setattr(peer, "try_request", fake_request)
    items, errors = peer.list_tools()
    assert items == []
    assert "repeated cursor" in errors[0]
    assert calls["n"] == 2


def test_peer_handles_unsolicited_request(monkeypatch, peer):
    peer.warnings.clear()
    incoming = {"jsonrpc": "2.0", "id": 99, "method": "sampling/createMessage", "params": {}}
    next_id = peer._next_id
    received = [incoming, {"jsonrpc": "2.0", "id": next_id, "result": {}}]
    monkeypatch.setattr(peer, "receive", lambda timeout=None: received.pop(0))
    response = peer.try_request("ping")
    assert response["result"] == {}
    assert any("unsolicited request" in warning for warning in peer.warnings)
