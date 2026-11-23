"""Static security audit: the part that finds the things nobody tests for.

Every rule here works on the manifest plus the negotiated capabilities. No
network probing is required beyond the handshake, which is why audit runs in CI
and against a laptop server identically. Each finding carries a stable rule id,
a severity, the evidence that triggered it, and a concrete fix.

The rules are deliberately specific. A finding that fires on every server is
noise; a finding that fires on a server interpolating tool arguments into a
shell command is worth the whole tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .manifest import Manifest, Tool


class Severity(str, Enum):
    """Finding severity, ordered from loudest to quietest."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Numeric weight used by scoring and sorting."""
        return {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}[self.value]

    @property
    def penalty(self) -> int:
        """Points removed from the security score."""
        return {"critical": 40, "high": 22, "medium": 10, "low": 4, "info": 0}[self.value]


@dataclass
class Finding:
    """One security observation with enough context to act on."""

    rule: str
    severity: Severity
    title: str
    evidence: str
    fix: str
    where: str = "server"

    def to_dict(self) -> dict:
        """JSON-ready form used by --format json."""
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "title": self.title,
            "evidence": self.evidence,
            "fix": self.fix,
            "where": self.where,
        }

    def line(self) -> str:
        """One terminal line."""
        return f"[{self.severity.value.upper():8}] {self.rule}  {self.title}"


@dataclass
class AuditReport:
    """The complete audit result."""

    findings: list[Finding] = field(default_factory=list)
    rules_checked: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        """Security score from 0 to 100, deterministic from the findings."""
        total = sum(finding.severity.penalty for finding in self.findings)
        return max(0, min(100, 100 - total))

    def grade(self) -> str:
        """Letter grade for the score."""
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
        """Findings per severity."""
        out = {severity.value: 0 for severity in Severity}
        for finding in self.findings:
            out[finding.severity.value] += 1
        return out

    def failing(self, fail_on: Severity) -> list[Finding]:
        """Findings at or above the requested severity threshold."""
        return [f for f in self.findings if f.severity.rank >= fail_on.rank]


SHELL_TOKENS = (
    "subprocess",
    "os.system",
    "os.popen",
    "shell=true",
    "sh -c",
    "bash -c",
    "exec(",
    "eval(",
    "popen",
    "child_process",
    "execsync",
    "system(",
    "shell command",
    "shell script",
    "run command",
    "execute command",
    "powershell",
    "cmd.exe",
)

COMMAND_HINTS = (
    "command",
    "cmd",
    "script",
    "shell",
    "exec",
    "run",
    "query",
    "sql",
    "path",
    "url",
    "endpoint",
    "host",
)

SECRET_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\s*[:=]\s*[\"']?[A-Za-z0-9_-]{16,}"),
)

TRAVERSAL_PATTERNS = (
    re.compile(r"file://"),
    re.compile(r"\.\./"),
    re.compile(r"(?i)\{\{?\s*(path|file|filename|dir|directory)\s*\}?\}"),
    re.compile(r"(?i)uriTemplate.*file:"),
)

OVERBROAD_URIS = (
    re.compile(r"^file:///(?:[A-Za-z]:[\\/])?(?:[^/]*)?$"),
    re.compile(r"^file:///(?:home|Users|root|etc|var|srv)(?:/|$)"),
)

PROMPT_INJECTION_HINTS = (
    "ignore previous",
    "ignore all previous",
    "disregard the above",
    "you are now",
    "system prompt",
)

MUTATING_TOOL_HINTS = ("delete", "remove", "drop", "write", "create", "update", "exec", "kill")
SENSITIVE_READ_HINTS = ("read", "get", "fetch", "list", "query", "dump", "export")


def _text_of(tool: Tool) -> str:
    parts = [tool.name, tool.description, " ".join(sorted(tool.properties))]
    parts.extend(str(value) for value in tool.annotations.values())
    return " ".join(parts).lower()


def _rule_shell_injection(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    for tool in manifest.tools:
        lowered = _text_of(tool)
        if not any(token in lowered for token in SHELL_TOKENS):
            continue
        risky = [name for name in tool.properties if any(h in name.lower() for h in COMMAND_HINTS)]
        string_args = [
            name
            for name, schema in tool.properties.items()
            if isinstance(schema, dict) and schema.get("type") == "string"
        ]
        if not risky and not string_args:
            continue
        findings.append(
            Finding(
                rule="MCP201",
                severity=Severity.CRITICAL,
                title=(
                    f"tool {tool.name!r} mentions shell execution and takes free-form string input"
                ),
                evidence=(
                    "description/name matches "
                    + ", ".join(t for t in SHELL_TOKENS if t in lowered)
                    + "; string arguments: "
                    + (", ".join(sorted(string_args)) or "none")
                ),
                fix=(
                    "pass argv arrays to subprocess without shell=True, validate the argument "
                    "against an allowlist, and reject shell metacharacters"
                ),
                where=f"tool:{tool.name}",
            )
        )
    return findings


def _rule_secret_leak(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    for tool in manifest.tools:
        haystacks = [tool.description, str(tool.annotations)]
        for name, schema in tool.properties.items():
            haystacks.append(
                name
                + " "
                + str(schema.get("default", ""))
                + " "
                + str(schema.get("description", ""))
            )
        blob = " ".join(haystacks)
        for pattern in SECRET_PATTERNS:
            match = pattern.search(blob)
            if match:
                findings.append(
                    Finding(
                        rule="MCP202",
                        severity=Severity.CRITICAL,
                        title=f"tool {tool.name!r} leaks a credential-shaped string",
                        evidence=(
                            f"matched {pattern.pattern[:40]!r} near {match.group(0)[:12]!r}..."
                        ),
                        fix=(
                            "remove the literal from the descriptor; "
                            "read the credential from the environment at call time"
                        ),
                        where=f"tool:{tool.name}",
                    )
                )
                break
    return findings


def _rule_path_traversal(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    for item in list(manifest.resources) + list(manifest.resource_templates):
        uri = getattr(item, "uri", "") or getattr(item, "uri_template", "")
        blob = uri.strip()
        for pattern in TRAVERSAL_PATTERNS:
            if pattern.search(blob):
                findings.append(
                    Finding(
                        rule="MCP203",
                        severity=Severity.HIGH,
                        title=f"resource {item.name!r} exposes a file/system URI template",
                        evidence=f"uri={blob!r}",
                        fix=(
                            "serve resources from a fixed allowlisted root, resolve and verify the "
                            "real path stays inside it, and reject .. segments before reading"
                        ),
                        where=f"resource:{item.name}",
                    )
                )
                break
    return findings


def _rule_overbroad_root(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    for item in list(manifest.resources) + list(manifest.resource_templates):
        uri = getattr(item, "uri", "") or getattr(item, "uri_template", "")
        for pattern in OVERBROAD_URIS:
            match = pattern.search(uri)
            if match and len(match.group(0)) <= 24:
                findings.append(
                    Finding(
                        rule="MCP204",
                        severity=Severity.HIGH,
                        title=f"resource {item.name!r} advertises a filesystem root",
                        evidence=f"uri={uri!r} covers an entire tree, not a document",
                        fix="scope the resource to explicit files under a project directory",
                        where=f"resource:{item.name}",
                    )
                )
                break
    return findings


def _rule_unvalidated_string_args(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    for tool in manifest.tools:
        loose = []
        for name, schema in tool.properties.items():
            if not isinstance(schema, dict):
                continue
            if schema.get("type") != "string":
                continue
            constrained = any(
                key in schema for key in ("enum", "const", "pattern", "maxLength", "format")
            )
            if not constrained:
                loose.append(name)
        if loose and any(
            h in tool.name.lower() for h in ("exec", "run", "shell", "spawn", "command")
        ):
            findings.append(
                Finding(
                    rule="MCP205",
                    severity=Severity.HIGH,
                    title=f"tool {tool.name!r} accepts unbounded strings for execution",
                    evidence="no enum/pattern/maxLength on: " + ", ".join(sorted(loose)),
                    fix="add an enum of allowed values or a strict pattern plus maxLength",
                    where=f"tool:{tool.name}",
                )
            )
    return findings


def _rule_prompt_injection_surface(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    for tool in manifest.tools:
        lowered = tool.description.lower()
        for hint in PROMPT_INJECTION_HINTS:
            if hint in lowered:
                findings.append(
                    Finding(
                        rule="MCP206",
                        severity=Severity.MEDIUM,
                        title=(
                            f"tool {tool.name!r} description carries instruction-override phrasing"
                        ),
                        evidence=(
                            f"description contains {hint!r}, "
                            "which an agent may treat as a directive"
                        ),
                        fix=(
                            "move behavioural guidance into the server's instructions "
                            "and keep descriptions factual"
                        ),
                        where=f"tool:{tool.name}",
                    )
                )
                break
    return findings


def _rule_capability_mismatch(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    capabilities = manifest.capabilities if isinstance(manifest.capabilities, dict) else {}
    if manifest.tools and "tools" not in capabilities:
        findings.append(
            Finding(
                rule="MCP207",
                severity=Severity.HIGH,
                title="server lists tools without advertising the tools capability",
                evidence=(
                    f"capabilities={sorted(capabilities) or 'none'}, tools={len(manifest.tools)}"
                ),
                fix='declare "tools": {} in the initialize result',
                where="capabilities",
            )
        )
    if (manifest.resources or manifest.resource_templates) and "resources" not in capabilities:
        findings.append(
            Finding(
                rule="MCP207",
                severity=Severity.HIGH,
                title="server lists resources without advertising the resources capability",
                evidence=f"capabilities={sorted(capabilities) or 'none'}",
                fix='declare "resources": {} in the initialize result',
                where="capabilities",
            )
        )
    if manifest.prompts and "prompts" not in capabilities:
        findings.append(
            Finding(
                rule="MCP207",
                severity=Severity.HIGH,
                title="server lists prompts without advertising the prompts capability",
                evidence=f"capabilities={sorted(capabilities) or 'none'}",
                fix='declare "prompts": {} in the initialize result',
                where="capabilities",
            )
        )
    return findings


def _rule_overbroad_tool(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    for tool in manifest.tools:
        verbs = {hint for hint in MUTATING_TOOL_HINTS if hint in tool.name.lower()}
        if len(verbs) >= 3:
            findings.append(
                Finding(
                    rule="MCP208",
                    severity=Severity.MEDIUM,
                    title=f"tool {tool.name!r} bundles many destructive verbs",
                    evidence="name matches " + ", ".join(sorted(verbs)),
                    fix="split into one tool per action so annotations and approvals can differ",
                    where=f"tool:{tool.name}",
                )
            )
        if len(tool.properties) > 20:
            findings.append(
                Finding(
                    rule="MCP208",
                    severity=Severity.MEDIUM,
                    title=f"tool {tool.name!r} takes {len(tool.properties)} arguments",
                    evidence="large flat argument surface is hard for a model to fill correctly",
                    fix="group arguments into a nested object or split the tool",
                    where=f"tool:{tool.name}",
                )
            )
    return findings


def _rule_missing_annotations(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    for tool in manifest.tools:
        destructive = any(
            h in tool.name.lower() for h in ("delete", "remove", "drop", "exec", "write")
        )
        has_hint = bool(tool.annotations) and (
            "destructiveHint" in tool.annotations
            or "readOnlyHint" in tool.annotations
            or "idempotentHint" in tool.annotations
        )
        if destructive and not has_hint:
            findings.append(
                Finding(
                    rule="MCP209",
                    severity=Severity.MEDIUM,
                    title=f"tool {tool.name!r} is destructive but declares no annotations",
                    evidence="annotations=" + (str(sorted(tool.annotations)) or "none"),
                    fix='add "annotations": {"destructiveHint": true, "readOnlyHint": false}',
                    where=f"tool:{tool.name}",
                )
            )
    return findings


def _rule_no_output_schema(manifest: Manifest) -> list[Finding]:
    untyped = [tool.name for tool in manifest.tools if tool.output_schema is None]
    if not untyped or len(untyped) < len(manifest.tools):
        return []
    return [
        Finding(
            rule="MCP210",
            severity=Severity.LOW,
            title="no tool declares an outputSchema",
            evidence=f"{len(untyped)} of {len(manifest.tools)} tools return an untyped payload",
            fix="add outputSchema so clients can validate results and models can plan on structure",
            where="tools",
        )
    ]


def _rule_unsigned_instructions(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    instructions = manifest.instructions
    if not instructions:
        return findings
    if len(instructions) > 4000:
        findings.append(
            Finding(
                rule="MCP211",
                severity=Severity.LOW,
                title="server instructions are very long",
                evidence=f"{len(instructions)} characters will be injected into every conversation",
                fix="keep instructions under ~2k characters and move detail into a resource",
                where="instructions",
            )
        )
    for pattern in SECRET_PATTERNS:
        match = pattern.search(instructions)
        if match:
            findings.append(
                Finding(
                    rule="MCP211",
                    severity=Severity.CRITICAL,
                    title="server instructions contain a credential-shaped string",
                    evidence=f"matched {match.group(0)[:12]!r}...",
                    fix="never put credentials in instructions; they are sent to every client",
                    where="instructions",
                )
            )
            break
    return findings


def _rule_tool_name_hygiene(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    seen: dict[str, int] = {}
    pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,63}$")
    for tool in manifest.tools:
        seen[tool.name] = seen.get(tool.name, 0) + 1
        if not pattern.match(tool.name):
            findings.append(
                Finding(
                    rule="MCP212",
                    severity=Severity.LOW,
                    title=f"tool name {tool.name!r} is not a stable identifier",
                    evidence="names should match [a-zA-Z][a-zA-Z0-9_.-]{0,63}",
                    fix="rename with lowercase snake_case and keep it stable across releases",
                    where=f"tool:{tool.name}",
                )
            )
    for name, count in sorted(seen.items()):
        if count > 1:
            findings.append(
                Finding(
                    rule="MCP212",
                    severity=Severity.HIGH,
                    title=f"duplicate tool name {name!r}",
                    evidence=f"advertised {count} times; clients key on the name",
                    fix="ensure each tools/list entry has a unique name",
                    where=f"tool:{name}",
                )
            )
    return findings


def _rule_readonly_mutation(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    for tool in manifest.tools:
        annotations = tool.annotations or {}
        if annotations.get("readOnlyHint") is True and any(
            hint in tool.name.lower() for hint in MUTATING_TOOL_HINTS
        ):
            findings.append(
                Finding(
                    rule="MCP213",
                    severity=Severity.MEDIUM,
                    title=f"tool {tool.name!r} is annotated readOnly but named like a mutation",
                    evidence="readOnlyHint=true, name contains mutation verb",
                    fix="fix the annotation: clients use readOnlyHint to skip approval prompts",
                    where=f"tool:{tool.name}",
                )
            )
    return findings


RULES = (
    "MCP201 shell-injection surface",
    "MCP202 credential leak in tool metadata",
    "MCP203 path traversal resource template",
    "MCP204 over-broad filesystem root",
    "MCP205 unvalidated execution strings",
    "MCP206 prompt-injection phrasing in descriptions",
    "MCP207 capability advertisement mismatch",
    "MCP208 over-broad tool surface",
    "MCP209 missing destructive annotations",
    "MCP210 missing outputSchema",
    "MCP211 unsafe server instructions",
    "MCP212 tool name hygiene",
    "MCP213 readOnly annotation contradicts name",
)


def audit(manifest: Manifest) -> AuditReport:
    """Run every static rule against a manifest and return the findings."""
    report = AuditReport(rules_checked=len(RULES))
    checks = (
        _rule_shell_injection,
        _rule_secret_leak,
        _rule_path_traversal,
        _rule_overbroad_root,
        _rule_unvalidated_string_args,
        _rule_prompt_injection_surface,
        _rule_capability_mismatch,
        _rule_overbroad_tool,
        _rule_missing_annotations,
        _rule_no_output_schema,
        _rule_unsigned_instructions,
        _rule_tool_name_hygiene,
        _rule_readonly_mutation,
    )
    for check in checks:
        report.findings.extend(check(manifest))
    report.findings.sort(key=lambda f: (-f.severity.rank, f.rule, f.where))
    return report
