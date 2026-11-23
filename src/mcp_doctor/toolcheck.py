"""test-tool: execute one real tool call and verify the answer against its schema.

Three questions are answered separately, because they fail for different reasons:

1. Is the call well formed? The arguments are validated against the tool's
   inputSchema before anything is sent, so a typo costs nothing.
2. Does the server accept it? The JSON-RPC round trip either succeeds or returns
   an error object, which is reported verbatim.
3. Is the answer contract-shaped? tools/call results must be
   {content: [...], isError?: bool}; when the tool declares an outputSchema the
   structuredContent payload is validated against it too.

That third question is the one nobody else asks, and it is where servers quietly
break their clients.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .client import Peer
from .errors import ServerError, TransportError, UsageError
from .jsonschema import SchemaError, validate_arguments, validate_schema
from .manifest import Tool

CONTENT_TYPES = {"text", "image", "audio", "resource", "resource_link"}


@dataclass
class CallResult:
    """Everything we learned from one test-tool run."""

    tool: str
    arguments: dict
    ok: bool
    stages: list[tuple[str, bool, str]] = field(default_factory=list)
    result: dict | None = None

    def add(self, stage: str, passed: bool, detail: str) -> None:
        """Record one stage outcome."""
        self.stages.append((stage, passed, detail))
        if not passed:
            self.ok = False

    def to_dict(self) -> dict:
        """JSON-ready form."""
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "ok": self.ok,
            "stages": [
                {"stage": stage, "passed": passed, "detail": detail}
                for stage, passed, detail in self.stages
            ],
            "result": self.result,
        }


def parse_arguments(raw: str | None) -> dict:
    """Parse the --args value, accepting JSON or @path and rejecting non-objects."""
    if raw is None or raw.strip() == "":
        return {}
    text = raw
    if raw.startswith("@"):
        try:
            with open(raw[1:], encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            raise UsageError("cannot read --args file " + repr(raw[1:]) + ": " + str(exc)) from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(
            "--args is not valid JSON ("
            + exc.msg
            + " at char "
            + str(exc.pos)
            + '). Example: --args \'{"a": 1, "b": 2}\''
        ) from exc
    if not isinstance(value, dict):
        raise UsageError("--args must be a JSON object, e.g. '{\"a\": 1}'")
    return value


def test_tool(peer: Peer, tool_name: str, arguments: dict, tools: list[Tool]) -> CallResult:
    """Run the verification stages against a live peer."""
    outcome = CallResult(tool=tool_name, arguments=arguments, ok=True)
    match = next((tool for tool in tools if tool.name == tool_name), None)
    if match is None:
        names = ", ".join(tool.name for tool in tools) or "none"
        raise UsageError("server has no tool " + repr(tool_name) + "; available: " + names)

    schema_errors = validate_schema(match.input_schema, tool_name)
    if schema_errors:
        first = "; ".join(str(error) for error in schema_errors[:3])
        outcome.add("schema", False, "the tool's own inputSchema is invalid: " + first)
        return outcome
    argument_errors: list[SchemaError] = validate_arguments(match.input_schema, arguments)
    if argument_errors:
        detail = "; ".join(error.path + ": " + error.message for error in argument_errors[:5])
        outcome.add("arguments", False, detail)
        return outcome
    outcome.add("arguments", True, "arguments satisfy inputSchema")

    try:
        result = peer.call_tool(tool_name, arguments)
    except ServerError as exc:
        outcome.add("call", False, str(exc))
        return outcome
    except TransportError as exc:
        outcome.add("call", False, "transport failed: " + str(exc))
        return outcome
    outcome.add("call", True, "server accepted the request")
    outcome.result = result

    content = result.get("content")
    if not isinstance(content, list):
        outcome.add("result", False, "tools/call result has no content array")
        return outcome
    if not content:
        outcome.add("result", False, "content array is empty")
        return outcome
    unknown = [
        item.get("type")
        for item in content
        if isinstance(item, dict) and item.get("type") not in CONTENT_TYPES
    ]
    if unknown:
        outcome.add("result", False, "unknown content types: " + str(sorted(set(unknown))))
        return outcome
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            outcome.add("result", False, "content[" + str(index) + "] is not an object")
            return outcome
        if item.get("type") == "text" and not isinstance(item.get("text"), str):
            outcome.add("result", False, "content[" + str(index) + "] text item has no text field")
            return outcome
    outcome.add("result", True, str(len(content)) + " content item(s), all well formed")

    if match.output_schema:
        structured = result.get("structuredContent")
        if structured is None:
            outcome.add(
                "outputSchema",
                False,
                "tool declares an outputSchema but returned no structuredContent",
            )
        else:
            errors = validate_arguments(match.output_schema, structured)
            if errors:
                detail = "; ".join(error.path + ": " + error.message for error in errors[:5])
                outcome.add("outputSchema", False, detail)
            else:
                outcome.add("outputSchema", True, "structuredContent matches outputSchema")
    else:
        outcome.add("outputSchema", True, "tool declares no outputSchema, nothing to check")
    return outcome


def render_call(outcome: CallResult, style) -> str:
    """Render a CallResult for the terminal."""
    lines: list[str] = []
    green = "\x1b[32m"
    red = "\x1b[31m"
    grey = "\x1b[90m"
    status = style.paint("PASS", green) if outcome.ok else style.paint("FAIL", red)
    arguments = json.dumps(outcome.arguments, ensure_ascii=False, sort_keys=True)
    lines.append(style.bold("test-tool " + outcome.tool + " " + arguments))
    for stage, passed, detail in outcome.stages:
        marker = style.paint("ok  ", green) if passed else style.paint("fail", red)
        lines.append("  " + marker + "  " + stage.ljust(12) + " " + detail)
    if outcome.result is not None:
        pretty = json.dumps(outcome.result, indent=2, ensure_ascii=False, sort_keys=True)
        rendered = pretty.splitlines()
        for line in rendered[:22]:
            lines.append(style.paint("    " + line, grey))
        if len(rendered) > 22:
            lines.append(style.paint("    ... " + str(len(rendered) - 22) + " more line(s)", grey))
    lines.append("")
    lines.append(status + "  test-tool " + outcome.tool)
    return "\n".join(lines)
