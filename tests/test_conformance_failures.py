"""Conformance failure-path tests: one deliberately broken server per defect.

The bundled buggy server is a good demo but it breaks several things at once, so
it cannot prove that an individual check fails for the right reason. This module
spawns a fixture server whose single behaviour is selected by a mode argument, and
asserts the specific check that must catch it. That is what keeps the scorecard
trustworthy: every FAIL path is exercised by a server that really is broken.
"""

from __future__ import annotations

import sys

import pytest

from mcp_doctor.client import connect_stdio
from mcp_doctor.conformance import FAIL, PASS, SKIP, WARN, run_conformance
from mcp_doctor.manifest import build_manifest

SERVER = """
import json
import sys

MODE = sys.argv[1]

TOOLS = [
    {
        "name": "good",
        "description": "A well formed tool.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "annotations": {"readOnlyHint": True},
    }
]

RESOURCES = [{"uri": "memo://doc", "name": "Doc", "mimeType": "text/plain"}]
PROMPTS = [{"name": "greet", "description": "Greet", "arguments": [{"name": "who"}]}]

CAPABILITIES = {"tools": {}, "resources": {}, "prompts": {}}


def initialize_result():
    result = {
        "protocolVersion": "2025-06-18",
        "capabilities": CAPABILITIES,
        "serverInfo": {"name": "fixture", "version": "0.0.1"},
        "instructions": "fixture server",
    }
    if MODE == "no_server_info":
        del result["serverInfo"]
    if MODE == "unknown_protocol":
        result["protocolVersion"] = "1999-01-01"
    if MODE == "bad_capability":
        CAPABILITIES["tools"] = "yes please"
    if MODE == "lying_capabilities":
        result["capabilities"] = {}
    if MODE == "no_instructions":
        del result["instructions"]
    return result


def tools_payload():
    if MODE == "duplicate_tool_names":
        return {"tools": [TOOLS[0], dict(TOOLS[0])]}
    if MODE == "missing_input_schema":
        return {"tools": [{"name": "noSchema", "description": "x"}]}
    if MODE == "missing_description":
        return {"tools": [{"name": "quiet", "inputSchema": {"type": "object"}}]}
    if MODE == "bad_input_schema":
        return {"tools": [{"name": "broken", "description": "x",
                           "inputSchema": {"type": "object", "required": ["a"],
                                           "properties": {"b": {"type": "nonsense"}}}}]}
    if MODE == "required_not_in_properties":
        return {"tools": [{"name": "mismatch", "description": "x",
                           "inputSchema": {"type": "object", "required": ["ghost"],
                                           "properties": {"real": {"type": "string"}}}}]}
    if MODE == "bad_output_schema":
        return {"tools": [dict(TOOLS[0], outputSchema={"type": "wat"})]}
    if MODE == "empty_tools":
        return {"tools": []}
    return {"tools": TOOLS}


def resources_payload():
    if MODE == "resource_without_uri":
        return {"resources": [{"name": "nameless"}]}
    return {"resources": RESOURCES}


def templates_payload():
    if MODE == "template_without_variable":
        return {"resourceTemplates": [{"uriTemplate": "memo://fixed", "name": "Fixed"}]}
    return {"resourceTemplates": []}


def prompts_payload():
    if MODE == "prompt_without_name":
        return {"prompts": [{"description": "anonymous"}]}
    if MODE == "prompt_argument_without_name":
        return {"prompts": [{"name": "p", "arguments": [{"description": "who"}]}]}
    return {"prompts": PROMPTS}


def call_payload():
    if MODE == "call_not_object":
        return {"content": "nope"}
    if MODE == "call_bad_content_type":
        return {"content": [{"type": "hologram"}]}
    return {"content": [{"type": "text", "text": "hi"}]}


def handle(request):
    method = request.get("method")
    rid = request.get("id")
    params = request.get("params") or {}
    def ok(payload):
        return {"jsonrpc": "2.0", "id": rid, "result": payload}
    def err(code, message):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok(initialize_result())
    if method == "ping":
        if MODE == "bad_ping":
            return ok({"pong": True})
        return ok({})
    if method == "tools/list":
        return ok(tools_payload())
    if method == "resources/list":
        return ok(resources_payload())
    if method == "resources/templates/list":
        return ok(templates_payload())
    if method == "prompts/list":
        return ok(prompts_payload())
    if method == "tools/call":
        if MODE == "wrong_invalid_params":
            return err(-32603, "internal error instead of invalid params")
        if not params.get("name"):
            return err(-32602, "invalid params: name is required")
        return ok(call_payload())
    if MODE == "wrong_error_code" and method == "mcp-doctor/does-not-exist":
        return err(-32000, "server error")
    if MODE == "silent_unknown_method":
        return ok({})
    return err(-32601, "method not found: " + str(method))


def emit(payload):
    sys.stdout.write(json.dumps(payload) + chr(10))
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        emit({"jsonrpc": "2.0", "id": None,
              "error": {"code": -32700, "message": "parse error: " + exc.msg}})
        continue
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "bad"}})
        continue
    if "id" not in request:
        continue
    emit(handle(request))
"""


@pytest.fixture(scope="module")
def server_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("conformance_fixture") / "server.py"
    path.write_text(SERVER, encoding="utf-8")
    return path


def run_mode(server_path, mode: str):
    command = '"' + sys.executable + '" "' + str(server_path) + '" ' + mode
    peer = connect_stdio(command)
    try:
        manifest = build_manifest(peer)
        return run_conformance(peer, manifest)
    finally:
        peer.close()


def status_of(card, check_id: str) -> str:
    return next(check.status for check in card.checks if check.id == check_id)


def test_fixture_baseline_passes(server_path):
    card = run_mode(server_path, "ok")
    assert card.failures() == []
    assert card.score >= 95


def test_missing_server_info_fails_init_02(server_path):
    card = run_mode(server_path, "no_server_info")
    assert status_of(card, "INIT-02") == FAIL
    assert "serverInfo.name missing" in next(c for c in card.checks if c.id == "INIT-02").detail


def test_unknown_protocol_warns_init_03(server_path):
    card = run_mode(server_path, "unknown_protocol")
    assert status_of(card, "INIT-03") == WARN
    assert "1999-01-01" in next(c for c in card.checks if c.id == "INIT-03").detail


def test_bad_capability_shape_fails_init_04(server_path):
    card = run_mode(server_path, "bad_capability")
    assert status_of(card, "INIT-04") == FAIL
    assert "must be an object" in next(c for c in card.checks if c.id == "INIT-04").detail


def test_lying_capabilities_fail_cap_02(server_path):
    card = run_mode(server_path, "lying_capabilities")
    assert status_of(card, "CAP-02") == FAIL
    assert "tools" in next(c for c in card.checks if c.id == "CAP-02").detail


def test_bad_ping_result_warns(server_path):
    card = run_mode(server_path, "bad_ping")
    assert status_of(card, "PROTO-01") == WARN


def test_wrong_error_code_fails_unknown_method(server_path):
    card = run_mode(server_path, "wrong_error_code")
    check = next(c for c in card.checks if c.id == "PROTO-02")
    assert check.status == FAIL
    assert "-32000" in check.detail


def test_wrong_invalid_params_code_warns(server_path):
    card = run_mode(server_path, "wrong_invalid_params")
    assert status_of(card, "PROTO-04") == WARN


def test_silent_unknown_method_fails(server_path):
    card = run_mode(server_path, "silent_unknown_method")
    assert status_of(card, "PROTO-02") == FAIL


def test_duplicate_tool_names_fail(server_path):
    card = run_mode(server_path, "duplicate_tool_names")
    assert status_of(card, "TOOLS-02") == FAIL
    assert "good" in next(c for c in card.checks if c.id == "TOOLS-02").detail


def test_missing_input_schema_fails_tool_shape(server_path):
    card = run_mode(server_path, "missing_input_schema")
    assert status_of(card, "TOOLS-01") == FAIL


def test_missing_description_warns_tool_shape(server_path):
    card = run_mode(server_path, "missing_description")
    assert status_of(card, "TOOLS-01") == WARN


def test_invalid_input_schema_fails(server_path):
    card = run_mode(server_path, "bad_input_schema")
    assert status_of(card, "TOOLS-03") == FAIL
    assert "nonsense" in next(c for c in card.checks if c.id == "TOOLS-03").detail


def test_required_property_mismatch_fails(server_path):
    card = run_mode(server_path, "required_not_in_properties")
    assert status_of(card, "TOOLS-04") == FAIL
    assert "ghost" in next(c for c in card.checks if c.id == "TOOLS-04").detail


def test_invalid_output_schema_fails(server_path):
    card = run_mode(server_path, "bad_output_schema")
    assert status_of(card, "TOOLS-05") == FAIL


def test_empty_tools_warns(server_path):
    card = run_mode(server_path, "empty_tools")
    assert status_of(card, "TOOLS-02") == WARN


def test_resource_without_uri_fails(server_path):
    card = run_mode(server_path, "resource_without_uri")
    assert status_of(card, "RES-01") == FAIL


def test_template_without_variable_fails(server_path):
    card = run_mode(server_path, "template_without_variable")
    assert status_of(card, "RES-01") == FAIL
    assert "no URI variable" in next(c for c in card.checks if c.id == "RES-01").detail


def test_prompt_without_name_fails(server_path):
    card = run_mode(server_path, "prompt_without_name")
    assert status_of(card, "PROMPT-01") == FAIL


def test_prompt_argument_without_name_fails(server_path):
    card = run_mode(server_path, "prompt_argument_without_name")
    assert status_of(card, "PROMPT-01") == FAIL


def test_missing_instructions_warns(server_path):
    card = run_mode(server_path, "no_instructions")
    assert status_of(card, "META-02") == WARN


def test_bad_content_type_in_safe_call_warns(server_path):
    """An unknown content type is suspicious rather than fatal.

    The check keeps this at WARN on purpose: the content-type vocabulary has grown
    across protocol revisions, so an unknown type may mean a newer server rather
    than a broken one. A missing content array, below, is a hard FAIL.
    """
    card = run_mode(server_path, "call_bad_content_type")
    check = next(c for c in card.checks if c.id == "CALL-01")
    assert check.status == WARN
    assert "hologram" in check.detail


def test_call_result_without_content_fails(server_path):
    card = run_mode(server_path, "call_not_object")
    assert status_of(card, "CALL-01") == FAIL


def test_score_drops_with_failures(server_path):
    healthy = run_mode(server_path, "ok")
    broken = run_mode(server_path, "bad_input_schema")
    assert broken.score < healthy.score
    assert broken.grade() in ("B", "C", "D", "F")


def test_skips_are_not_counted_against_the_score(server_path):
    card = run_mode(server_path, "ok")
    skipped = [check for check in card.checks if check.status == SKIP]
    for check in skipped:
        assert check.earned == 0.0
    applicable = [check for check in card.checks if check.status != SKIP]
    assert len(applicable) + len(skipped) == len(card.checks)


def test_every_check_has_a_meaningful_pass(server_path):
    """A check that never passes on a healthy server is a broken check."""
    card = run_mode(server_path, "ok")
    for check in card.checks:
        assert check.status in (PASS, SKIP), (
            check.id + " failed on a healthy fixture: " + check.detail
        )
