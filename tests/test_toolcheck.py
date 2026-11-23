"""toolcheck tests: every failure stage, driven by real servers, not mocks.

The fixture server below is a real stdio MCP server whose behaviour is selected
by a mode argument. That keeps the tests honest: the code under test is exercised
through the same transport, handshake and content-type checks as production.
"""

from __future__ import annotations

import json
import sys

import pytest

from mcp_doctor.client import connect_stdio
from mcp_doctor.errors import UsageError
from mcp_doctor.manifest import build_manifest
from mcp_doctor.report import Style
from mcp_doctor.toolcheck import parse_arguments, render_call
from mcp_doctor.toolcheck import test_tool as run_tool_check

SERVER = """
import json
import sys

MODE = sys.argv[1]

TOOL = {
    "name": "probe",
    "description": "Return a probe payload.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}
if MODE == "bad_schema":
    TOOL["inputSchema"] = {"type": "nonsense"}
if MODE == "typed_output":
    TOOL["outputSchema"] = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }


def call_result():
    if MODE == "no_content":
        return {}
    if MODE == "empty_content":
        return {"content": []}
    if MODE == "bad_content_type":
        return {"content": [{"type": "hologram", "data": "x"}]}
    if MODE == "non_object_content":
        return {"content": ["just a string"]}
    if MODE == "text_without_text":
        return {"content": [{"type": "text"}]}
    if MODE == "typed_output":
        return {
            "content": [{"type": "text", "text": "1"}],
            "structuredContent": {"n": "not-an-int"},
        }
    if MODE == "typed_output_ok":
        return {"content": [{"type": "text", "text": "1"}], "structuredContent": {"n": 1}}
    if MODE == "server_error":
        return None
    return {"content": [{"type": "text", "text": "ok"}]}


def handle(request):
    method = request.get("method")
    rid = request.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fixture", "version": "0.0.1"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [TOOL]}}
    if method == "tools/call":
        if MODE == "server_error":
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "exploded"}}
        return {"jsonrpc": "2.0", "id": rid, "result": call_result()}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "nope"}}


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    if "id" not in request:
        continue
    sys.stdout.write(json.dumps(handle(request)) + chr(10))
    sys.stdout.flush()
"""


@pytest.fixture()
def make_server(tmp_path):
    """Write the fixture server and return a factory that yields a connected peer."""
    path = tmp_path / "fixture_server.py"
    path.write_text(SERVER, encoding="utf-8")
    opened = []

    def factory(mode: str):
        command = '"' + sys.executable + '" "' + str(path) + '" ' + mode
        peer = connect_stdio(command)
        opened.append(peer)
        return peer

    yield factory
    for peer in opened:
        peer.close()


def run(make_server, mode: str, arguments=None):
    peer = make_server(mode)
    manifest = build_manifest(peer)
    return run_tool_check(peer, "probe", arguments or {"text": "hi"}, manifest.tools)


def test_happy_path(make_server):
    outcome = run(make_server, "ok")
    assert outcome.ok
    stages = [stage for stage, passed, _ in outcome.stages if passed]
    assert stages == ["arguments", "call", "result", "outputSchema"]


def test_declared_output_schema_is_validated(make_server):
    outcome = run(make_server, "typed_output")
    assert not outcome.ok
    failing = [entry for entry in outcome.stages if not entry[1]]
    assert failing[0][0] == "outputSchema"
    assert "$.n: expected integer, got string" in failing[0][2]


def test_declared_output_schema_passes_when_correct(make_server):
    outcome = run(make_server, "typed_output_ok")
    assert outcome.ok
    assert json.loads(json.dumps(outcome.to_dict()))["ok"] is True


def test_invalid_tool_schema_is_caught_before_sending(make_server):
    outcome = run(make_server, "bad_schema")
    assert not outcome.ok
    assert outcome.stages[0][0] == "schema"
    assert "unknown type" in outcome.stages[0][2]


def test_server_error_is_reported_verbatim(make_server):
    outcome = run(make_server, "server_error")
    assert not outcome.ok
    assert outcome.stages[-1][0] == "call"
    assert "exploded" in outcome.stages[-1][2]
    assert "-32000" in outcome.stages[-1][2]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("no_content", "no content array"),
        ("empty_content", "content array is empty"),
        ("bad_content_type", "unknown content types"),
        ("non_object_content", "is not an object"),
        ("text_without_text", "has no text field"),
    ],
)
def test_malformed_content_is_caught(make_server, mode, expected):
    outcome = run(make_server, mode)
    assert not outcome.ok
    assert any(expected in detail for _, passed, detail in outcome.stages if not passed)


def test_unknown_tool_lists_the_available_ones(make_server):
    peer = make_server("ok")
    manifest = build_manifest(peer)
    with pytest.raises(UsageError) as excinfo:
        run_tool_check(peer, "nope", {}, manifest.tools)
    assert "available: probe" in str(excinfo.value)


def test_render_marks_the_failing_stage(make_server):
    outcome = run(make_server, "empty_content")
    text = render_call(outcome, Style(colour=False))
    assert "fail" in text
    assert "FAIL  test-tool probe" in text
    assert "content array is empty" in text


def test_render_truncates_long_results(make_server):
    outcome = run(make_server, "ok")
    outcome.result = {"content": [{"type": "text", "text": str(index)} for index in range(40)]}
    text = render_call(outcome, Style(colour=False))
    assert "more line(s)" in text


def test_parse_arguments_variants(tmp_path):
    assert parse_arguments(None) == {}
    assert parse_arguments("   ") == {}
    assert parse_arguments('{"a": 1}') == {"a": 1}
    path = tmp_path / "args.json"
    path.write_text('{"b": 2}', encoding="utf-8")
    assert parse_arguments("@" + str(path)) == {"b": 2}
    with pytest.raises(UsageError) as excinfo:
        parse_arguments("@" + str(tmp_path / "missing.json"))
    assert "cannot read --args file" in str(excinfo.value)
    with pytest.raises(UsageError) as excinfo:
        parse_arguments("[1, 2]")
    assert "must be a JSON object" in str(excinfo.value)
