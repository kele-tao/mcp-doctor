"""Terminal, Markdown, JSON and HTML rendering of everything mcp-doctor produces.

Rendering is separated from analysis so the same result objects can feed a
terminal, a CI comment and a self-contained HTML page without drifting. Colours
are disabled automatically when stdout is not a TTY or NO_COLOR is set, which is
what makes the JSON and Markdown outputs safe to diff.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass

TICK = chr(96)

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
GREY = "\x1b[90m"

STATUS_COLOURS = {"PASS": GREEN, "WARN": YELLOW, "FAIL": RED, "SKIP": GREY}
SEVERITY_COLOURS = {
    "critical": MAGENTA,
    "high": RED,
    "medium": YELLOW,
    "low": BLUE,
    "info": GREY,
}


@dataclass
class Style:
    """Colour policy for one render."""

    colour: bool = True

    def paint(self, text: str, colour: str) -> str:
        """Wrap text in an ANSI colour when colour is enabled."""
        if not self.colour:
            return text
        return colour + text + RESET

    def bold(self, text: str) -> str:
        """Bold text when colour is enabled."""
        if not self.colour:
            return text
        return BOLD + text + RESET


def visible_len(text: str) -> int:
    """Length of a string ignoring ANSI escapes, for table alignment."""
    length = 0
    index = 0
    while index < len(text):
        if text[index] == "\x1b":
            while index < len(text) and text[index] != "m":
                index += 1
            index += 1
            continue
        length += 1
        index += 1
    return length


def pad(text: str, width: int, align: str = "left") -> str:
    """Pad a possibly-coloured string to a visible width."""
    missing = max(0, width - visible_len(text))
    if align == "right":
        return " " * missing + text
    return text + " " * missing


def truncate(text: str, width: int) -> str:
    """Truncate to width with an ellipsis."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "..."


def table(headers: list[str], rows: list[list[str]], style: Style | None = None) -> str:
    """Render a box-free aligned table that stays readable with colour codes."""
    style = style or Style(colour=False)
    widths = [visible_len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], visible_len(cell))
    header_line = "  ".join(
        style.bold(pad(header, widths[index])) for index, header in enumerate(headers)
    )
    separator = "  ".join(style.paint("-" * width, GREY) for width in widths)
    body = "\n".join(
        "  ".join(pad(cell, widths[index]) for index, cell in enumerate(row)) for row in rows
    )
    return header_line + "\n" + separator + ("\n" + body if body else "")


def bar(value: int, width: int = 24, style: Style | None = None) -> str:
    """Text progress bar used by the scorecard."""
    style = style or Style(colour=False)
    filled = max(0, min(width, round(width * value / 100)))
    colour = GREEN if value >= 85 else YELLOW if value >= 60 else RED
    return style.paint("#" * filled, colour) + style.paint("." * (width - filled), GREY)


def score_line(label: str, value: int, width: int = 24, style: Style | None = None) -> str:
    """Render a label, a bar and the numeric score on one line."""
    style = style or Style(colour=False)
    return pad(label, 14) + " " + bar(value, width, style) + " " + str(value).rjust(3) + "/100"


def group_checks(card) -> list[tuple[str, int, int, int, int]]:
    """Collapse checks into (group, pass, warn, fail, skip) rows.

    Terminal readers care about two things: did anything break, and which area is
    weakest. Twenty-four identical PASS lines answer neither, so the default view
    groups by check-id prefix and only lists the checks that did not pass.
    """
    order: list[str] = []
    buckets: dict[str, list[int]] = {}
    for check in card.checks:
        group = check.id.split("-")[0] if "-" in check.id else check.id
        if group not in buckets:
            buckets[group] = [0, 0, 0, 0]
            order.append(group)
        index = {"PASS": 0, "WARN": 1, "FAIL": 2, "SKIP": 3}.get(check.status, 3)
        buckets[group][index] += 1
    return [(group, *buckets[group]) for group in order]


def render_scorecard(card, style: Style, verbose: bool = False) -> str:
    """Render a Scorecard for the terminal.

    Without --verbose the suite is summarised by area and only non-passing
    checks are listed with their detail; with --verbose every check is a row.
    """
    lines: list[str] = []
    lines.append(style.bold(card.server + "  |  protocol " + card.protocol))
    counts = card.counts()
    lines.append(score_line("conformance", card.score, 28, style) + "   grade " + card.grade())
    summary = (
        style.paint(str(counts["PASS"]) + " pass", GREEN)
        + "  "
        + style.paint(str(counts["WARN"]) + " warn", YELLOW)
        + "  "
        + style.paint(str(counts["FAIL"]) + " fail", RED)
        + "  "
        + style.paint(str(counts["SKIP"]) + " skip", GREY)
    )
    lines.append(summary)
    lines.append("")
    if verbose:
        rows = []
        for check in card.checks:
            painted = style.paint(check.status.ljust(4), STATUS_COLOURS.get(check.status, GREY))
            rows.append([check.id, painted, truncate(check.name, 52), truncate(check.detail, 46)])
        lines.append(table(["ID", "STATUS", "CHECK", "DETAIL"], rows, style))
        return "\n".join(lines)
    group_rows = []
    for group, passed, warned, failed, skipped in group_checks(card):
        cells = []
        cells.append(
            style.paint(str(passed) + " pass", GREEN) if passed else style.paint("-", GREY)
        )
        if warned:
            cells.append(style.paint(str(warned) + " warn", YELLOW))
        if failed:
            cells.append(style.paint(str(failed) + " fail", RED))
        if skipped:
            cells.append(style.paint(str(skipped) + " skip", GREY))
        group_rows.append([group, "  ".join(cells)])
    lines.append(table(["AREA", "RESULT"], group_rows, style))
    interesting = [check for check in card.checks if check.status != "PASS"]
    if interesting:
        lines.append("")
        lines.append(style.bold("not passing"))
        for check in interesting:
            painted = style.paint(check.status.ljust(4), STATUS_COLOURS.get(check.status, GREY))
            lines.append(
                "  "
                + painted
                + "  "
                + check.id.ljust(9)
                + " "
                + truncate(check.name, 54)
                + ("  (" + truncate(check.detail, 60) + ")" if check.detail else "")
            )
    return "\n".join(lines)


def render_audit(report, style: Style, verbose: bool = False) -> str:
    """Render an AuditReport for the terminal."""
    lines: list[str] = []
    counts = report.counts()
    lines.append(style.bold("security audit"))
    lines.append(score_line("security", report.score, 28, style) + "   grade " + report.grade())
    lines.append(
        "  ".join(
            style.paint(str(counts[key]) + " " + key, SEVERITY_COLOURS[key])
            for key in ("critical", "high", "medium", "low", "info")
        )
        + "   ("
        + str(report.rules_checked)
        + " static rules evaluated)"
    )
    lines.append("")
    if not report.findings:
        lines.append(
            style.paint("  no findings: nothing in the advertised surface matches a rule", GREEN)
        )
        return "\n".join(lines)
    rows = []
    for finding in report.findings:
        severity = style.paint(
            finding.severity.value.upper().ljust(8), SEVERITY_COLOURS[finding.severity.value]
        )
        rows.append(
            [finding.rule, severity, truncate(finding.title, 58), truncate(finding.where, 26)]
        )
    lines.append(table(["RULE", "SEVERITY", "FINDING", "WHERE"], rows, style))
    lines.append("")
    for finding in report.findings:
        lines.append(style.bold("  " + finding.rule + " " + finding.title))
        lines.append(style.paint("    evidence: " + finding.evidence, GREY))
        if verbose or finding.severity.rank >= 4:
            lines.append(style.paint("    fix:      " + finding.fix, CYAN))
    return "\n".join(lines)


def render_inspect(manifest, style: Style, verbose: bool = False) -> str:
    """Render the inspection view: header, capabilities, tools, resources, prompts."""
    lines: list[str] = []
    info = manifest
    lines.append(
        style.bold((info.server_name + " " + info.server_version).strip())
        + "   protocol "
        + style.paint(info.protocol_version, CYAN)
    )
    counts = info.counts()
    lines.append(
        style.paint(str(counts["tools"]), BOLD)
        + " tools   "
        + style.paint(str(counts["resources"]), BOLD)
        + " resources   "
        + style.paint(str(counts["resourceTemplates"]), BOLD)
        + " templates   "
        + style.paint(str(counts["prompts"]), BOLD)
        + " prompts"
    )
    capabilities = ", ".join(sorted(info.capabilities)) or "none"
    lines.append(style.paint("capabilities: " + capabilities, GREY))
    if info.instructions and info.instructions.strip():
        snippet = info.instructions.strip().splitlines()[0]
        lines.append(style.paint("instructions: " + truncate(snippet, 88), GREY))
    lines.append("")
    if info.tools:
        lines.append(style.bold("TOOLS"))
        rows = []
        for tool in info.tools:
            first_line = tool.description.splitlines()[0] if tool.description.strip() else "-"
            rows.append(
                [
                    style.paint(tool.name, CYAN),
                    truncate(first_line, 44),
                    str(len(tool.properties)),
                    ",".join(tool.required) or "-",
                    "yes" if tool.output_schema else "no",
                ]
            )
        lines.append(table(["NAME", "DESCRIPTION", "ARGS", "REQUIRED", "OUTPUT"], rows, style))
        if verbose:
            lines.append("")
            for tool in info.tools:
                lines.append(style.paint("  " + tool.signature(), DIM))
        lines.append("")
    if info.resources:
        lines.append(style.bold("RESOURCES"))
        rows = [
            [style.paint(item.uri, CYAN), truncate(item.name, 34), item.mime_type or "-"]
            for item in info.resources
        ]
        lines.append(table(["URI", "NAME", "MIME"], rows, style))
        lines.append("")
    if info.resource_templates:
        lines.append(style.bold("RESOURCE TEMPLATES"))
        rows = [
            [style.paint(item.uri_template, CYAN), truncate(item.name, 34)]
            for item in info.resource_templates
        ]
        lines.append(table(["URI TEMPLATE", "NAME"], rows, style))
        lines.append("")
    if info.prompts:
        lines.append(style.bold("PROMPTS"))
        rows = []
        for prompt in info.prompts:
            first_line = prompt.description.splitlines()[0] if prompt.description.strip() else "-"
            rows.append(
                [
                    style.paint(prompt.name, CYAN),
                    truncate(first_line, 44),
                    str(len(prompt.arguments)) + " args",
                ]
            )
        lines.append(table(["NAME", "DESCRIPTION", "ARGUMENTS"], rows, style))
        lines.append("")
    if info.warnings:
        lines.append(style.paint("warnings:", YELLOW))
        for warning in info.warnings:
            lines.append(style.paint("  - " + warning, YELLOW))
    return "\n".join(lines).rstrip() + "\n"


def render_manifest_markdown(manifest) -> str:
    """Generate the reference documentation a README would otherwise hand-write."""
    fence = TICK * 3
    out: list[str] = []
    out.append(("# " + manifest.server_name + " " + manifest.server_version).rstrip())
    out.append("")
    out.append("Protocol version: " + manifest.protocol_version)
    out.append("")
    out.append("## Capabilities")
    out.append("")
    for name in sorted(manifest.capabilities):
        block = manifest.capabilities[name]
        flags = ", ".join(f"{k}={v}" for k, v in sorted(block.items())) if block else "no options"
        out.append("- " + name + " (" + flags + ")")
    if not manifest.capabilities:
        out.append("- none advertised")
    out.append("")
    if manifest.instructions:
        out.append("## Instructions")
        out.append("")
        out.append("> " + manifest.instructions.strip().replace("\n", "\n> "))
        out.append("")
    out.append("## Tools")
    out.append("")
    if not manifest.tools:
        out.append("No tools advertised.")
        out.append("")
    for tool in manifest.tools:
        out.append("### " + tool.name)
        out.append("")
        if tool.description:
            out.append(tool.description.strip())
            out.append("")
        out.append("Signature: " + TICK + tool.signature() + TICK)
        out.append("")
        if tool.properties:
            out.append("| Argument | Type | Required | Description |")
            out.append("| --- | --- | --- | --- |")
            for name, schema in tool.properties.items():
                kind = schema.get("type", "any") if isinstance(schema, dict) else "any"
                if isinstance(kind, list):
                    kind = " / ".join(str(item) for item in kind)
                description = ""
                if isinstance(schema, dict):
                    description = str(schema.get("description", "") or "")
                required = "yes" if name in tool.required else "no"
                out.append(
                    "| "
                    + TICK
                    + name
                    + TICK
                    + " | "
                    + str(kind)
                    + " | "
                    + required
                    + " | "
                    + description
                    + " |"
                )
            out.append("")
        if tool.output_schema:
            out.append("Output schema:")
            out.append("")
            out.append(fence + "json")
            out.append(json.dumps(tool.output_schema, indent=2, sort_keys=True))
            out.append(fence)
            out.append("")
    out.append("## Resources")
    out.append("")
    if not manifest.resources:
        out.append("No static resources advertised.")
        out.append("")
    for item in manifest.resources:
        suffix = " (" + item.mime_type + ")" if item.mime_type else ""
        out.append("- " + TICK + item.uri + TICK + " - " + item.name + suffix)
    out.append("")
    if manifest.resource_templates:
        out.append("## Resource Templates")
        out.append("")
        for item in manifest.resource_templates:
            out.append("- " + TICK + item.uri_template + TICK + " - " + item.name)
        out.append("")
    out.append("## Prompts")
    out.append("")
    if not manifest.prompts:
        out.append("No prompts advertised.")
        out.append("")
    for prompt in manifest.prompts:
        out.append("### " + prompt.name)
        out.append("")
        if prompt.description:
            out.append(prompt.description.strip())
            out.append("")
        if prompt.arguments:
            out.append("| Argument | Required | Description |")
            out.append("| --- | --- | --- |")
            for argument in prompt.arguments:
                required = "yes" if argument.get("required") else "no"
                out.append(
                    "| "
                    + TICK
                    + str(argument.get("name"))
                    + TICK
                    + " | "
                    + required
                    + " | "
                    + str(argument.get("description", ""))
                    + " |"
                )
            out.append("")
    out.append("---")
    out.append("")
    out.append("Generated by [mcp-doctor](https://github.com/kele-tao/mcp-doctor).")
    out.append("")
    return "\n".join(out)


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 40px 28px 64px; background: #0b0f17; color: #d1d5db;
       font: 15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.wrap { max-width: 1040px; margin: 0 auto; }
h1 { font-size: 26px; margin: 0 0 4px; color: #f9fafb; letter-spacing: -0.02em; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .14em; color: #8b9bb4;
     margin: 38px 0 12px; font-weight: 600; }
.sub { color: #8b9bb4; font-size: 14px; margin-bottom: 26px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; }
.card { background: #121826; border: 1px solid #1f2937; border-radius: 12px; padding: 18px; }
.card .label { color: #8b9bb4; font-size: 11px; letter-spacing: .14em; text-transform: uppercase; }
.card .value { font-size: 38px; font-weight: 700; color: #f9fafb;
              line-height: 1.15; margin: 6px 0 2px; }
.card .meta { color: #8b9bb4; font-size: 12px; }
.bar { height: 8px; border-radius: 999px; background: #1f2937; overflow: hidden; margin-top: 12px; }
.bar > span { display: block; height: 100%; border-radius: 999px; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th { text-align: left; color: #8b9bb4; font-weight: 600; font-size: 11px; letter-spacing: .1em;
     text-transform: uppercase; padding: 8px 10px; border-bottom: 1px solid #1f2937; }
td { padding: 9px 10px; border-bottom: 1px solid #151c2b; vertical-align: top; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12.5px; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px;
        font-weight: 700; letter-spacing: .06em; }
.pass { background: rgba(74,222,128,.14); color: #4ade80; }
.warn { background: rgba(251,191,36,.14); color: #fbbf24; }
.fail { background: rgba(255,95,95,.16); color: #ff8787; }
.skip { background: rgba(107,114,128,.18); color: #9ca3af; }
.critical { background: rgba(192,132,252,.16); color: #c084fc; }
.high { background: rgba(255,95,95,.16); color: #ff8787; }
.medium { background: rgba(251,191,36,.14); color: #fbbf24; }
.low { background: rgba(96,165,250,.14); color: #93c5fd; }
.info { background: rgba(107,114,128,.18); color: #9ca3af; }
.evidence { color: #8b9bb4; font-size: 12.5px; }
.fix { color: #a7f3d0; font-size: 12.5px; }
.empty { color: #4ade80; font-size: 14px; }
footer { margin-top: 44px; color: #4b5563; font-size: 12px; }
"""


def render_html(card, audit_report, manifest, transcript: list[str] | None = None) -> str:
    """Build a single self-contained HTML report: no CDN, no external assets."""

    def colour_for(score: int) -> str:
        if score >= 85:
            return "#4ade80"
        if score >= 60:
            return "#fbbf24"
        return "#ff5f5f"

    counts = card.counts()
    parts: list[str] = []
    parts.append('<!doctype html><html lang="en"><head><meta charset="utf-8">')
    parts.append("<title>mcp-doctor report - " + _esc(card.server) + "</title>")
    parts.append("<style>" + CSS + '</style></head><body><div class="wrap">')
    parts.append("<h1>mcp-doctor report</h1>")
    parts.append(
        '<div class="sub">'
        + _esc(card.server)
        + " &middot; protocol "
        + '<span class="mono">'
        + _esc(card.protocol)
        + "</span> &middot; "
        + str(len(card.checks))
        + " conformance checks &middot; "
        + str(audit_report.rules_checked)
        + " security rules</div>"
    )
    parts.append('<div class="cards">')
    cards = (
        (
            "Conformance",
            card.score,
            "grade "
            + card.grade()
            + " &middot; "
            + str(counts["PASS"])
            + " pass / "
            + str(counts["FAIL"])
            + " fail",
        ),
        (
            "Security",
            audit_report.score,
            "grade "
            + audit_report.grade()
            + " &middot; "
            + str(len(audit_report.findings))
            + " findings",
        ),
        (
            "Surface",
            len(manifest.tools),
            str(len(manifest.resources))
            + " resources &middot; "
            + str(len(manifest.prompts))
            + " prompts",
        ),
    )
    for label, value, meta in cards:
        parts.append('<div class="card">')
        parts.append('<div class="label">' + _esc(label) + "</div>")
        parts.append('<div class="value">' + _esc(value) + "</div>")
        parts.append('<div class="meta">' + meta + "</div>")
        if label != "Surface":
            parts.append(
                '<div class="bar"><span style="width:'
                + str(int(value))
                + "%;background:"
                + colour_for(int(value))
                + '"></span></div>'
            )
        parts.append("</div>")
    parts.append("</div>")

    parts.append("<h2>Conformance checks</h2>")
    parts.append(
        "<table><thead><tr><th>ID</th><th>Check</th><th>Status</th><th>Detail</th>"
        "</tr></thead><tbody>"
    )
    for check in card.checks:
        parts.append(
            '<tr><td class="mono">'
            + _esc(check.id)
            + "</td><td>"
            + _esc(check.name)
            + '</td><td><span class="pill '
            + check.status.lower()
            + '">'
            + _esc(check.status)
            + '</span></td><td class="evidence">'
            + _esc(check.detail)
            + "</td></tr>"
        )
    parts.append("</tbody></table>")

    parts.append("<h2>Security findings</h2>")
    if not audit_report.findings:
        parts.append(
            '<p class="empty">No findings: nothing in the advertised surface matched a rule.</p>'
        )
    else:
        parts.append(
            "<table><thead><tr><th>Rule</th><th>Severity</th><th>Finding</th><th>Evidence</th>"
            "<th>Fix</th></tr></thead><tbody>"
        )
        for finding in audit_report.findings:
            parts.append(
                '<tr><td class="mono">'
                + _esc(finding.rule)
                + "</td>"
                + '<td><span class="pill '
                + finding.severity.value
                + '">'
                + _esc(finding.severity.value.upper())
                + "</span></td>"
                + "<td>"
                + _esc(finding.title)
                + '<div class="evidence">'
                + _esc(finding.where)
                + "</div></td>"
                + '<td class="evidence mono">'
                + _esc(finding.evidence)
                + "</td>"
                + '<td class="fix">'
                + _esc(finding.fix)
                + "</td></tr>"
            )
        parts.append("</tbody></table>")

    parts.append("<h2>Advertised surface</h2>")
    if manifest.tools:
        parts.append(
            "<table><thead><tr><th>Tool</th><th>Signature</th><th>Description</th>"
            "</tr></thead><tbody>"
        )
        for tool in manifest.tools:
            parts.append(
                '<tr><td class="mono">'
                + _esc(tool.name)
                + "</td>"
                + '<td class="mono evidence">'
                + _esc(tool.signature())
                + "</td>"
                + "<td>"
                + _esc(tool.description)
                + "</td></tr>"
            )
        parts.append("</tbody></table>")
    else:
        parts.append('<p class="empty">No tools advertised.</p>')

    if transcript:
        parts.append("<h2>Protocol transcript</h2>")
        parts.append("<table><thead><tr><th>#</th><th>Exchange</th></tr></thead><tbody>")
        for index, line in enumerate(transcript, 1):
            parts.append(
                '<tr><td class="mono">'
                + str(index).rjust(2, "0")
                + '</td><td class="mono evidence">'
                + _esc(line)
                + "</td></tr>"
            )
        parts.append("</tbody></table>")

    parts.append(
        "<footer>Generated by mcp-doctor 1.0.0 &middot; no external assets &middot; "
        "no network calls beyond the server under test</footer>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)
