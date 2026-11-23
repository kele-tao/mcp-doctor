"""The protocol conformance suite: 24 checks against a live MCP server.

Each check is a small function that talks to the server and returns a Check
with a verdict, so adding a rule is a three-line change. Checks are independent:
one broken area does not stop the rest of the scorecard, which is what makes the
report useful when a server is half-finished.

Verdicts: PASS, WARN (legal but sketchy), FAIL (spec violation or unreachable),
SKIP (not applicable to this server's declared capabilities).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import jsonrpc
from .client import SUPPORTED_PROTOCOL_VERSIONS, Peer
from .errors import ServerError, TransportError
from .jsonschema import schema_summary, validate_schema
from .manifest import Manifest

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass
class Check:
    """One conformance check result."""

    id: str
    name: str
    status: str
    detail: str = ""
    evidence: str = ""
    weight: int = 1

    @property
    def earned(self) -> float:
        """Weight earned by this check, used for the score."""
        if self.status == PASS:
            return float(self.weight)
        if self.status == WARN:
            return self.weight * 0.5
        return 0.0

    def to_dict(self) -> dict:
        """JSON-ready form."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
            "weight": self.weight,
        }


@dataclass
class Scorecard:
    """The full conformance result set plus the rolled-up score."""

    checks: list[Check] = field(default_factory=list)
    server: str = ""
    protocol: str = ""

    def add(self, check: Check) -> None:
        """Append a check result."""
        self.checks.append(check)

    def by_status(self, status: str) -> list[Check]:
        """All checks with a given verdict."""
        return [check for check in self.checks if check.status == status]

    @property
    def score(self) -> int:
        """Weighted conformance score from 0 to 100."""
        applicable = [c for c in self.checks if c.status != SKIP and c.weight > 0]
        if not applicable:
            return 0
        total = sum(c.weight for c in applicable)
        earned = sum(c.earned for c in applicable)
        return int(round(100 * earned / total))

    def grade(self) -> str:
        """Letter grade, consistent with the audit scoring."""
        score = self.score
        if score >= 95:
            return "A"
        if score >= 85:
            return "B"
        if score >= 70:
            return "C"
        if score >= 50:
            return "D"
        return "F"

    def counts(self) -> dict[str, int]:
        """Checks per verdict."""
        out = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for check in self.checks:
            out[check.status] = out.get(check.status, 0) + 1
        return out

    def failures(self) -> list[Check]:
        """The checks that failed, in execution order."""
        return self.by_status(FAIL)

    def to_dict(self) -> dict:
        """JSON-ready form used by --format json and the HTML report."""
        return {
            "server": self.server,
            "protocol": self.protocol,
            "score": self.score,
            "grade": self.grade(),
            "counts": self.counts(),
            "checks": [check.to_dict() for check in self.checks],
        }


class _Probe:
    """Small helper that turns protocol calls into check results."""

    def __init__(self, peer: Peer, manifest: Manifest) -> None:
        self.peer = peer
        self.manifest = manifest

    def supports(self, capability: str) -> bool:
        """True when the capability is advertised or was found by probing.

        A server that answers tools/list without declaring the tools capability
        is still inspected: skipping its tools would hide the very defect the
        audit reports, so the discovered capability is treated as present and
        the mismatch is scored by the capability checks instead.
        """
        return capability in self.manifest.capabilities or capability in self.manifest.undeclared

    def error_response(self, method: str, params: dict | None = None) -> dict:
        return self.peer.try_request(method, params)

    def call_nowait(self, method: str, params: dict | None = None) -> int:
        """Send a request without consuming its response; return the request id."""
        request_id = self.peer._next_id
        self.peer._next_id += 1
        self.peer.send(jsonrpc.make_request(request_id, method, params))
        return request_id

    def drain(self, limit: int = 8) -> list[dict]:
        """Consume whatever the server has already queued, without blocking long."""
        frames: list[dict] = []
        for _ in range(limit):
            try:
                frames.append(self.peer.receive(0.15))
            except TransportError:
                break
        return frames


def _check_initialize(probe: _Probe) -> Check:
    info = probe.peer.server_info
    if info is None:
        return Check("INIT-01", "initialize handshake", FAIL, "peer was never initialized")
    exchange = next(
        (e for e in probe.peer.exchanges if e.request.get("method") == "initialize"), None
    )
    if exchange is None or exchange.response is None:
        return Check("INIT-01", "initialize handshake", FAIL, "no initialize exchange recorded")
    result = exchange.response.get("result")
    if not isinstance(result, dict):
        return Check("INIT-01", "initialize handshake", FAIL, "initialize result is not an object")
    if not isinstance(result.get("protocolVersion"), str):
        return Check(
            "INIT-01", "initialize handshake", FAIL, "initialize result lacks protocolVersion"
        )
    if not isinstance(result.get("capabilities"), dict):
        return Check(
            "INIT-01", "initialize handshake", FAIL, "initialize result lacks a capabilities object"
        )
    return Check(
        "INIT-01",
        "initialize handshake",
        PASS,
        detail=f"negotiated {info.protocol_version}",
        evidence=json.dumps(result, ensure_ascii=False)[:160],
    )


def _check_server_info(probe: _Probe) -> Check:
    info = probe.peer.server_info
    assert info is not None
    if not info.server_name:
        return Check(
            "INIT-02", "serverInfo advertises name and version", FAIL, "serverInfo.name missing"
        )
    if not info.server_version:
        return Check(
            "INIT-02", "serverInfo advertises name and version", WARN, "serverInfo.version missing"
        )
    return Check(
        "INIT-02",
        "serverInfo advertises name and version",
        PASS,
        detail=f"{info.server_name} {info.server_version}",
    )


def _check_protocol_version(probe: _Probe) -> Check:
    info = probe.peer.server_info
    assert info is not None
    version = info.protocol_version
    if version in SUPPORTED_PROTOCOL_VERSIONS:
        return Check(
            "INIT-03", "protocol version is a published MCP revision", PASS, detail=version
        )
    return Check(
        "INIT-03",
        "protocol version is a published MCP revision",
        WARN,
        detail=f"unknown version {version!r}",
        evidence="known: " + ", ".join(SUPPORTED_PROTOCOL_VERSIONS),
    )


def _check_capability_shape(probe: _Probe) -> Check:
    info = probe.peer.server_info
    assert info is not None
    capabilities = info.capabilities
    if not isinstance(capabilities, dict):
        return Check(
            "INIT-04", "capability advertisement shape", FAIL, "capabilities is not an object"
        )
    problems = []
    for name in ("tools", "resources", "prompts", "logging"):
        if name in capabilities and not isinstance(capabilities[name], dict):
            problems.append(f"{name} must be an object")
    for name in ("resources",):
        block = capabilities.get(name)
        if isinstance(block, dict):
            for flag in ("subscribe", "listChanged"):
                if flag in block and not isinstance(block[flag], bool):
                    problems.append(f"{name}.{flag} must be a boolean")
    if problems:
        return Check("INIT-04", "capability advertisement shape", FAIL, detail="; ".join(problems))
    return Check(
        "INIT-04",
        "capability advertisement shape",
        PASS,
        detail="advertised: " + (", ".join(sorted(capabilities)) or "none"),
    )


def _check_ping(probe: _Probe) -> Check:
    response = probe.peer.ping()
    if "error" in response:
        return Check(
            "PROTO-01",
            "ping returns an empty result",
            FAIL,
            detail=f"error {response['error'].get('code')}: {response['error'].get('message')}",
        )
    result = response.get("result")
    if result != {}:
        return Check(
            "PROTO-01",
            "ping returns an empty result",
            WARN,
            detail=f"result is {result!r}, spec says an empty object",
        )
    return Check("PROTO-01", "ping returns an empty result", PASS)


def _check_unknown_method(probe: _Probe) -> Check:
    response = probe.peer.try_request("mcp-doctor/does-not-exist", {})
    if "error" not in response:
        return Check(
            "PROTO-02",
            "unknown method returns -32601",
            FAIL,
            detail="server answered a result instead of an error",
        )
    error = response["error"]
    if error.get("code") != jsonrpc.METHOD_NOT_FOUND:
        return Check(
            "PROTO-02",
            "unknown method returns -32601",
            FAIL,
            detail=f"code {error.get('code')!r} instead of -32601",
            evidence=str(error)[:160],
        )
    return Check(
        "PROTO-02", "unknown method returns -32601", PASS, detail="-32601 method not found"
    )


def _check_error_id_echo(probe: _Probe) -> Check:
    response = probe.peer.try_request("mcp-doctor/echo-test", None)
    if "error" not in response:
        return Check("PROTO-03", "error responses echo the request id", FAIL, "no error returned")
    if not probe.peer.exchanges:
        return Check(
            "PROTO-03", "error responses echo the request id", FAIL, "no exchange was recorded"
        )
    sent_id = probe.peer.exchanges[-1].request.get("id")
    if response.get("id") != sent_id:
        return Check(
            "PROTO-03",
            "error responses echo the request id",
            FAIL,
            detail=f"sent id {sent_id!r}, got {response.get('id')!r}",
        )
    return Check("PROTO-03", "error responses echo the request id", PASS, detail=f"id={sent_id}")


def _check_invalid_params(probe: _Probe) -> Check:
    response = probe.peer.try_request("tools/call", {})
    if "error" not in response:
        return Check(
            "PROTO-04", "missing params returns -32602", WARN, detail="server returned a result"
        )
    code = response["error"].get("code")
    if code != jsonrpc.INVALID_PARAMS:
        return Check(
            "PROTO-04",
            "missing params returns -32602",
            WARN,
            detail=f"code {code!r} instead of -32602",
            evidence=str(response["error"])[:160],
        )
    return Check("PROTO-04", "missing params returns -32602", PASS, detail="-32602 invalid params")


def _check_parse_error(probe: _Probe) -> Check:
    peer = probe.peer
    before = len(peer.exchanges)
    peer.transport.send_raw('{"jsonrpc": "2.0", "id": 999, "method": ')
    try:
        frame = peer.receive(3.0)
    except TransportError as exc:
        return Check(
            "PROTO-05",
            "malformed JSON returns -32700",
            FAIL,
            detail="no error response (server dropped the frame or closed): " + str(exc)[:100],
        )
    if "error" not in frame:
        return Check(
            "PROTO-05", "malformed JSON returns -32700", FAIL, detail=f"got {frame!r}"[:160]
        )
    code = frame["error"].get("code")
    if code != jsonrpc.PARSE_ERROR:
        return Check(
            "PROTO-05",
            "malformed JSON returns -32700",
            WARN,
            detail=f"code {code!r} instead of -32700",
            evidence=str(frame["error"])[:160],
        )
    del before
    return Check("PROTO-05", "malformed JSON returns -32700", PASS, detail="-32700 parse error")


def _check_invalid_envelope(probe: _Probe) -> Check:
    peer = probe.peer
    peer.transport.send_raw('{"id": 998, "method": "ping"}')
    try:
        frame = peer.receive(3.0)
    except TransportError as exc:
        return Check(
            "PROTO-06",
            "invalid envelope returns -32600",
            FAIL,
            detail="no error response: " + str(exc)[:100],
        )
    if "error" not in frame:
        return Check(
            "PROTO-06", "invalid envelope returns -32600", FAIL, detail=f"got {frame!r}"[:160]
        )
    code = frame["error"].get("code")
    if code not in (jsonrpc.INVALID_REQUEST, jsonrpc.PARSE_ERROR):
        return Check(
            "PROTO-06",
            "invalid envelope returns -32600",
            WARN,
            detail=f"code {code!r} instead of -32600",
        )
    return Check("PROTO-06", "invalid envelope returns -32600", PASS, detail=f"code {code}")


def _check_notification_silence(probe: _Probe) -> Check:
    peer = probe.peer
    peer.notify("notifications/mcp-doctor-probe", {"note": "ignore me"})
    response = peer.try_request("ping")
    if "error" in response:
        return Check(
            "PROTO-07",
            "unknown notifications are ignored",
            FAIL,
            detail=f"following ping failed: {response['error'].get('message')}",
        )
    return Check(
        "PROTO-07",
        "unknown notifications are ignored",
        PASS,
        detail="server kept serving after an unknown notification",
    )


def _check_cancellation(probe: _Probe) -> Check:
    peer = probe.peer
    request_id = probe.call_nowait("ping")
    peer.notify("notifications/cancelled", {"requestId": request_id, "reason": "conformance probe"})
    response = peer.try_request("ping")
    stale = probe.drain()
    for frame in stale:
        if "error" in frame and frame["error"].get("code") == jsonrpc.METHOD_NOT_FOUND:
            method_hint = str(frame["error"].get("message", ""))
            if "cancel" in method_hint.lower():
                return Check(
                    "PROTO-08",
                    "cancellation notification accepted",
                    FAIL,
                    detail="server answered notifications/cancelled with method-not-found",
                )
    if "error" in response:
        return Check(
            "PROTO-08",
            "cancellation notification accepted",
            FAIL,
            detail="server stopped responding after a cancellation",
        )
    return Check(
        "PROTO-08",
        "cancellation notification accepted",
        PASS,
        detail="server stayed responsive and ignored the cancellation notification",
    )


def _check_tools_shape(probe: _Probe) -> Check:
    if not probe.supports("tools"):
        return Check("TOOLS-01", "tools/list result shape", SKIP, detail="tools not advertised")
    for tool in probe.manifest.tools:
        if not tool.name:
            return Check("TOOLS-01", "tools/list result shape", FAIL, "a tool has no name")
        if not isinstance(tool.raw.get("inputSchema"), dict):
            return Check(
                "TOOLS-01",
                "tools/list result shape",
                FAIL,
                detail=f"tool {tool.name!r} has no object inputSchema",
            )
        if not isinstance(tool.raw.get("description"), str):
            return Check(
                "TOOLS-01",
                "tools/list result shape",
                WARN,
                detail=f"tool {tool.name!r} has no description",
            )
    return Check(
        "TOOLS-01",
        "tools/list result shape",
        PASS,
        detail=f"{len(probe.manifest.tools)} tools, all with object inputSchema",
    )


def _check_tool_names(probe: _Probe) -> Check:
    if not probe.supports("tools"):
        return Check(
            "TOOLS-02", "tool names are unique and non-empty", SKIP, detail="not advertised"
        )
    names = [tool.name for tool in probe.manifest.tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if not names:
        return Check("TOOLS-02", "tool names are unique and non-empty", WARN, detail="no tools")
    if duplicates:
        return Check(
            "TOOLS-02",
            "tool names are unique and non-empty",
            FAIL,
            detail="duplicates: " + ", ".join(duplicates),
        )
    return Check(
        "TOOLS-02", "tool names are unique and non-empty", PASS, detail=f"{len(names)} unique"
    )


def _check_input_schemas(probe: _Probe) -> Check:
    if not probe.supports("tools"):
        return Check("TOOLS-03", "inputSchema is valid JSON Schema", SKIP, detail="not advertised")
    problems: list[str] = []
    for tool in probe.manifest.tools:
        errors = validate_schema(tool.input_schema, tool.name)
        for error in errors[:3]:
            problems.append(f"{tool.name}: {error}")
    if problems:
        return Check(
            "TOOLS-03",
            "inputSchema is valid JSON Schema",
            FAIL,
            detail="; ".join(problems[:4]),
            evidence=f"{len(problems)} schema problem(s)",
        )
    return Check(
        "TOOLS-03",
        "inputSchema is valid JSON Schema",
        PASS,
        detail=f"{len(probe.manifest.tools)} schema(s) valid against the supported keyword set",
    )


def _check_required_properties(probe: _Probe) -> Check:
    if not probe.supports("tools"):
        return Check(
            "TOOLS-04", "required names exist in properties", SKIP, detail="not advertised"
        )
    problems = []
    for tool in probe.manifest.tools:
        missing = [name for name in tool.required if name not in tool.properties]
        if missing:
            problems.append(f"{tool.name}: required-but-undeclared {', '.join(missing)}")
    if problems:
        return Check(
            "TOOLS-04",
            "required names exist in properties",
            FAIL,
            detail="; ".join(problems[:4]),
        )
    return Check(
        "TOOLS-04",
        "required names exist in properties",
        PASS,
        detail="all required names are declared",
    )


def _check_output_schemas(probe: _Probe) -> Check:
    if not probe.supports("tools"):
        return Check("TOOLS-05", "outputSchema validity", SKIP, detail="not advertised")
    declared = [tool for tool in probe.manifest.tools if tool.output_schema is not None]
    if not declared:
        return Check(
            "TOOLS-05",
            "outputSchema validity",
            SKIP,
            detail="no tool declares an outputSchema",
        )
    problems = []
    for tool in declared:
        for error in validate_schema(tool.output_schema, tool.name):
            problems.append(f"{tool.name}: {error}")
    if problems:
        return Check("TOOLS-05", "outputSchema validity", FAIL, detail="; ".join(problems[:4]))
    return Check(
        "TOOLS-05",
        "outputSchema validity",
        PASS,
        detail=f"{len(declared)} outputSchema(s) valid",
    )


def _check_resources(probe: _Probe) -> Check:
    if not probe.supports("resources"):
        return Check("RES-01", "resources/list result shape", SKIP, detail="not advertised")
    for item in probe.manifest.resources:
        if not item.uri or not item.uri.startswith(("file:", "http:", "https:", "mcp:", "custom:")):
            if not item.uri:
                return Check("RES-01", "resources/list result shape", FAIL, "resource without uri")
    for template in probe.manifest.resource_templates:
        if not template.uri_template or "{" not in template.uri_template:
            return Check(
                "RES-01",
                "resources/list result shape",
                FAIL,
                detail=f"template {template.name!r} has no URI variable",
            )
    return Check(
        "RES-01",
        "resources/list result shape",
        PASS,
        detail=f"{len(probe.manifest.resources)} resources, "
        f"{len(probe.manifest.resource_templates)} templates",
    )


def _check_prompts(probe: _Probe) -> Check:
    if not probe.supports("prompts"):
        return Check("PROMPT-01", "prompts/list result shape", SKIP, detail="not advertised")
    for prompt in probe.manifest.prompts:
        if not prompt.name:
            return Check("PROMPT-01", "prompts/list result shape", FAIL, "prompt without name")
        for argument in prompt.arguments:
            if not isinstance(argument.get("name"), str):
                return Check(
                    "PROMPT-01",
                    "prompts/list result shape",
                    FAIL,
                    detail=f"prompt {prompt.name!r} has an argument without a name",
                )
    return Check(
        "PROMPT-01",
        "prompts/list result shape",
        PASS,
        detail=f"{len(probe.manifest.prompts)} prompts",
    )


def _check_capability_reachable(probe: _Probe) -> Check:
    manifests = {
        "tools": "tools/list",
        "resources": "resources/list",
        "prompts": "prompts/list",
    }
    problems = []
    for capability, method in manifests.items():
        if capability not in probe.manifest.capabilities:
            continue
        response = probe.peer.try_request(method)
        if "error" in response:
            problems.append(f"{capability}: {response['error'].get('message')}")
    if problems:
        return Check(
            "CAP-01",
            "advertised capabilities are reachable",
            FAIL,
            detail="; ".join(problems),
        )
    return Check(
        "CAP-01",
        "advertised capabilities are reachable",
        PASS,
        detail="every advertised list endpoint answered",
    )


def _check_capability_declared(probe: _Probe) -> Check:
    """A server must not serve lists it never advertised.

    Clients gate their behaviour on the initialize result: a client that trusts
    the advertisement will never call tools/list from a server that declares no
    tools capability. Serving it anyway means the server and its clients
    disagree about what it is, which is a real interoperability defect, not a
    style nit.
    """
    hidden = probe.manifest.undeclared
    if hidden:
        return Check(
            "CAP-02",
            "served capabilities are advertised",
            FAIL,
            detail="serves " + ", ".join(hidden) + " without declaring the capability",
            evidence="capabilities=" + (str(sorted(probe.manifest.capabilities)) or "none"),
            weight=2,
        )
    return Check(
        "CAP-02",
        "served capabilities are advertised",
        PASS,
        detail="no undeclared list endpoint answered",
    )


def _check_metrics(probe: _Probe) -> Check:
    if not probe.manifest.tools:
        return Check("META-01", "tool schemas are describable", SKIP, detail="no tools")
    undocumented = [tool.name for tool in probe.manifest.tools if not tool.description.strip()]
    if undocumented:
        return Check(
            "META-01",
            "tool schemas are describable",
            WARN,
            detail="missing descriptions: " + ", ".join(undocumented[:5]),
        )
    complexity = sum(
        schema_summary(tool.input_schema)["properties"] for tool in probe.manifest.tools
    )
    return Check(
        "META-01",
        "tool schemas are describable",
        PASS,
        detail=f"{complexity} declared arguments across {len(probe.manifest.tools)} tools",
    )


def _check_safe_call(probe: _Probe) -> Check:
    if not probe.supports("tools"):
        return Check("CALL-01", "safe tool call round-trip", SKIP, detail="not advertised")
    candidates = []
    for tool in probe.manifest.tools:
        if tool.required:
            continue
        annotations = tool.annotations or {}
        if annotations.get("readOnlyHint") is True or tool.name.lower().startswith(
            ("get", "list", "read", "search", "echo", "ping")
        ):
            candidates.append(tool)
    if not candidates:
        return Check(
            "CALL-01",
            "safe tool call round-trip",
            SKIP,
            detail="no read-only tool with zero required arguments to probe with",
        )
    tool_obj = candidates[0]
    try:
        result = probe.peer.call_tool(tool_obj.name, {})
    except ServerError as exc:
        return Check(
            "CALL-01",
            "safe tool call round-trip",
            FAIL,
            detail=f"{tool_obj.name}: {exc}",
        )
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return Check(
            "CALL-01",
            "safe tool call round-trip",
            FAIL,
            detail=f"{tool_obj.name} returned no content array",
            evidence=json.dumps(result, ensure_ascii=False)[:160],
        )
    kinds = {item.get("type") for item in content if isinstance(item, dict)}
    if kinds - {"text", "image", "audio", "resource", "resource_link"}:
        return Check(
            "CALL-01",
            "safe tool call round-trip",
            WARN,
            detail=f"unknown content types: {sorted(kinds)}",
        )
    return Check(
        "CALL-01",
        "safe tool call round-trip",
        PASS,
        detail=f"{tool_obj.name} returned {len(content)} content item(s)",
    )


def _check_instructions(probe: _Probe) -> Check:
    info = probe.peer.server_info
    assert info is not None
    instructions = info.instructions
    if instructions is None:
        return Check("META-02", "server instructions are present", WARN, detail="no instructions")
    if not isinstance(instructions, str):
        return Check(
            "META-02",
            "server instructions are present",
            FAIL,
            detail="instructions is not a string",
        )
    return Check(
        "META-02",
        "server instructions are present",
        PASS,
        detail=f"{len(instructions.split())} words",
    )


def _check_latency(probe: _Probe) -> Check:
    samples = [e.elapsed_ms for e in probe.peer.exchanges if e.response is not None][:20]
    if not samples:
        return Check("PERF-01", "round-trip latency", SKIP, detail="no exchanges recorded")
    worst = max(samples)
    if worst > 2000:
        return Check(
            "PERF-01",
            "round-trip latency",
            WARN,
            detail=f"slowest exchange {worst:.0f} ms over {len(samples)} samples",
        )
    average = sum(samples) / len(samples)
    return Check(
        "PERF-01",
        "round-trip latency",
        PASS,
        detail=f"average {average:.1f} ms, worst {worst:.1f} ms over {len(samples)} exchanges",
    )


CHECKS = (
    _check_initialize,
    _check_server_info,
    _check_protocol_version,
    _check_capability_shape,
    _check_ping,
    _check_unknown_method,
    _check_error_id_echo,
    _check_invalid_params,
    _check_parse_error,
    _check_invalid_envelope,
    _check_notification_silence,
    _check_cancellation,
    _check_tools_shape,
    _check_tool_names,
    _check_input_schemas,
    _check_required_properties,
    _check_output_schemas,
    _check_resources,
    _check_prompts,
    _check_capability_reachable,
    _check_capability_declared,
    _check_metrics,
    _check_safe_call,
    _check_instructions,
    _check_latency,
)


def run_conformance(peer: Peer, manifest: Manifest | None = None) -> Scorecard:
    """Run every conformance check against a connected peer.

    A crashed check becomes a FAIL with the exception text: an unreachable
    server should produce a scorecard, not a traceback.
    """
    if manifest is None:
        from .manifest import build_manifest

        manifest = build_manifest(peer)
    info = peer.server_info
    card = Scorecard(
        server=f"{info.server_name} {info.server_version}".strip() if info else "unknown",
        protocol=info.protocol_version if info else "unknown",
    )
    probe = _Probe(peer, manifest)
    for check in CHECKS:
        try:
            card.add(check(probe))
        except (TransportError, ServerError) as exc:
            card.add(
                Check(
                    _fallback_id(len(card.checks)),
                    f"check {check.__name__} could not run",
                    FAIL,
                    detail=str(exc)[:300],
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad check must not kill the suite
            card.add(
                Check(
                    _fallback_id(len(card.checks)),
                    f"check {check.__name__} crashed",
                    FAIL,
                    detail=f"{type(exc).__name__}: {exc}"[:300],
                )
            )
    return card


def _fallback_id(index: int) -> str:
    """Stable id for a check that could not produce its own result."""
    return f"ERR-{index + 1:02d}"
