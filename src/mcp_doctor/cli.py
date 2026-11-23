"""Command line interface for mcp-doctor.

Exit codes are part of the contract and are documented in the README:

  0  everything checked out
  1  a check FAILed, a finding hit the --fail-on threshold, or the server could
     not be reached at all
  2  the command itself was wrong (bad flag, bad JSON, missing file, no target)

Every user error is printed as "error: ..." with a next step, never a traceback.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .audit import Severity, audit
from .client import Peer, connect_http, connect_stdio
from .conformance import run_conformance
from .discovery import discover, render_discovery
from .errors import McpDoctorError, TransportError, UsageError
from .manifest import build_manifest
from .report import (
    Style,
    render_audit,
    render_html,
    render_inspect,
    render_manifest_markdown,
    render_scorecard,
)
from .toolcheck import parse_arguments, render_call, test_tool

STDIO_HELP = 'spawn a stdio server, e.g. --stdio "python examples/demo_server/server.py"'
HTTP_HELP = "connect to a Streamable HTTP endpoint, e.g. --http http://127.0.0.1:8000/mcp"


def _style(args: argparse.Namespace) -> Style:
    if getattr(args, "no_color", False):
        return Style(colour=False)
    if os.environ.get("NO_COLOR"):
        return Style(colour=False)
    return Style(colour=sys.stdout.isatty())


def _target_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stdio", metavar="CMD", help=STDIO_HELP)
    parser.add_argument("--http", metavar="URL", help=HTTP_HELP)
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        metavar="SECONDS",
        help="per-request timeout (default: 15)",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="disable ANSI colours in terminal output"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show evidence and fixes")


def _open(args: argparse.Namespace) -> Peer:
    if not args.stdio and not args.http:
        raise UsageError('give a server to talk to: --stdio "<command>" or --http <url>')
    if args.stdio and args.http:
        raise UsageError("--stdio and --http are mutually exclusive")
    if args.timeout <= 0:
        raise UsageError("--timeout must be greater than zero")
    try:
        peer = (
            connect_stdio(args.stdio, timeout=args.timeout)
            if args.stdio
            else connect_http(args.http, timeout=args.timeout)
        )
    except TransportError as exc:
        raise TransportError(str(exc) + _hint_for(args)) from exc
    return peer


def _hint_for(args: argparse.Namespace) -> str:
    if args.stdio:
        return (
            "\nhint: run the command by hand to see the server's own error output, "
            "e.g. " + args.stdio
        )
    return "\nhint: check the URL is reachable and the endpoint is the MCP path, not the base URL"


def _write(path_text: str, content: str) -> None:
    path = Path(path_text)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _emit(args: argparse.Namespace, payload: dict, markdown: str | None, text: str) -> None:
    fmt = getattr(args, "format", "text")
    if fmt == "json":
        body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    elif fmt == "md":
        if markdown is None:
            raise UsageError("this command does not support --format md")
        body = markdown
    else:
        body = text
    out = getattr(args, "out", None)
    if out:
        _write(out, body)
        print("wrote " + out)
    else:
        print(body.rstrip())


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_inspect(args: argparse.Namespace) -> int:
    style = _style(args)
    peer = _open(args)
    try:
        manifest = build_manifest(peer)
        card = run_conformance(peer, manifest)
        security = audit(manifest)
        if args.format == "json":
            payload = {
                "manifest": manifest.to_dict(),
                "conformance": card.to_dict(),
                "security": {
                    "score": security.score,
                    "grade": security.grade(),
                    "findings": [finding.to_dict() for finding in security.findings],
                },
            }
            print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        elif args.format == "md":
            print(render_manifest_markdown(manifest).rstrip())
        else:
            print(render_inspect(manifest, style, args.verbose))
            print(render_scorecard(card, style, args.verbose))
            print("")
            print(render_audit(security, style, args.verbose))
        if args.docs:
            _write(args.docs, render_manifest_markdown(manifest))
            print("wrote " + args.docs, file=sys.stderr)
        if args.manifest:
            _write(args.manifest, manifest.to_json())
            print("wrote " + args.manifest, file=sys.stderr)
        if args.html:
            _write(
                args.html,
                render_html(card, security, manifest, [e.summary() for e in peer.exchanges]),
            )
            print("wrote " + args.html, file=sys.stderr)
        if card.failures():
            return 1
        if args.fail_under is not None and card.score < args.fail_under:
            print(
                "conformance score "
                + str(card.score)
                + " is below --fail-under "
                + str(args.fail_under),
                file=sys.stderr,
            )
            return 1
        return 0
    finally:
        peer.close()


def cmd_audit(args: argparse.Namespace) -> int:
    style = _style(args)
    peer = _open(args)
    try:
        manifest = build_manifest(peer)
        report = audit(manifest)
        threshold = Severity(args.fail_on)
        hits = report.failing(threshold)
        if args.format == "json":
            payload = {
                "server": manifest.server_name,
                "score": report.score,
                "grade": report.grade(),
                "counts": report.counts(),
                "threshold": threshold.value,
                "findings": [finding.to_dict() for finding in report.findings],
            }
            print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        elif args.format == "md":
            print(_audit_markdown(report, manifest))
        else:
            print(render_audit(report, style, args.verbose))
        if args.html:
            card = run_conformance(peer, manifest)
            _write(
                args.html,
                render_html(card, report, manifest, [e.summary() for e in peer.exchanges]),
            )
            print("wrote " + args.html, file=sys.stderr)
        if hits:
            print(
                str(len(hits)) + " finding(s) at or above " + threshold.value + "; failing the run",
                file=sys.stderr,
            )
            return 1
        return 0
    finally:
        peer.close()


def _audit_markdown(report, manifest) -> str:
    lines = ["# Security audit: " + manifest.server_name, ""]
    lines.append(
        "Score **"
        + str(report.score)
        + "/100** (grade "
        + report.grade()
        + "), "
        + str(len(report.findings))
        + " finding(s) across "
        + str(report.rules_checked)
        + " rules."
    )
    lines.append("")
    if not report.findings:
        lines.append("No findings.")
        return "\n".join(lines)
    lines.append("| Rule | Severity | Finding | Evidence | Fix |")
    lines.append("| --- | --- | --- | --- | --- |")
    for finding in report.findings:
        lines.append(
            "| "
            + finding.rule
            + " | "
            + finding.severity.value
            + " | "
            + finding.title.replace("|", "/")
            + " | "
            + finding.evidence.replace("|", "/")
            + " | "
            + finding.fix.replace("|", "/")
            + " |"
        )
    return "\n".join(lines)


def cmd_conformance(args: argparse.Namespace) -> int:
    style = _style(args)
    peer = _open(args)
    try:
        manifest = build_manifest(peer)
        card = run_conformance(peer, manifest)
        if args.format == "json":
            print(json.dumps(card.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
        elif args.format == "md":
            lines = ["| ID | Check | Status | Detail |", "| --- | --- | --- | --- |"]
            for check in card.checks:
                lines.append(
                    "| "
                    + check.id
                    + " | "
                    + check.name
                    + " | "
                    + check.status
                    + " | "
                    + check.detail.replace("|", "/")
                    + " |"
                )
            print("\n".join(lines))
        else:
            print(render_scorecard(card, style, args.verbose))
        if args.html:
            _write(
                args.html,
                render_html(card, audit(manifest), manifest, [e.summary() for e in peer.exchanges]),
            )
            print("wrote " + args.html, file=sys.stderr)
        if card.failures():
            return 1
        if args.fail_under is not None and card.score < args.fail_under:
            print(
                "conformance score "
                + str(card.score)
                + " is below --fail-under "
                + str(args.fail_under),
                file=sys.stderr,
            )
            return 1
        return 0
    finally:
        peer.close()


def cmd_test_tool(args: argparse.Namespace) -> int:
    style = _style(args)
    arguments = parse_arguments(args.args)
    if not args.tool:
        raise UsageError("--tool NAME is required, e.g. --tool add")
    peer = _open(args)
    try:
        manifest = build_manifest(peer)
        if not manifest.tools:
            raise UsageError("server advertises no tools; nothing to call")
        outcome = test_tool(peer, args.tool, arguments, manifest.tools)
        if args.format == "json":
            print(json.dumps(outcome.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(render_call(outcome, style))
        return 0 if outcome.ok else 1
    finally:
        peer.close()


def cmd_discover(args: argparse.Namespace) -> int:
    style = _style(args)
    report = discover(explicit=args.config)
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(render_discovery(report, style))
    if not report.configs:
        return 1
    return 1 if report.broken else 0


def cmd_report(args: argparse.Namespace) -> int:
    peer = _open(args)
    try:
        manifest = build_manifest(peer)
        card = run_conformance(peer, manifest)
        security = audit(manifest)
        html_text = render_html(card, security, manifest, [e.summary() for e in peer.exchanges])
        out = args.out or "mcp-doctor-report.html"
        _write(out, html_text)
        print("wrote " + out)
        if args.format == "json":
            print(json.dumps(card.to_dict(), indent=2, sort_keys=True))
        return 1 if card.failures() else 0
    finally:
        peer.close()


def cmd_init(args: argparse.Namespace) -> int:
    from .generate import generate_demo_server

    target = Path(args.directory)
    written = generate_demo_server(target)
    print("wrote " + str(len(written)) + " file(s) to " + str(target))
    for path in written:
        print("  " + str(path.relative_to(target.parent)))
    print("")
    print("run it with:")
    print('  python -m mcp_doctor inspect --stdio "python ' + str(target / "server.py") + '"')
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-doctor",
        description="Lint, test and audit Model Context Protocol servers.",
        epilog="Exit codes: 0 ok, 1 failed check or findings above --fail-on, 2 usage error.",
    )
    parser.add_argument("--version", action="version", version="mcp-doctor " + __version__)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    inspect_parser = subparsers.add_parser(
        "inspect", help="list tools/resources/prompts and score the protocol surface"
    )
    _target_flags(inspect_parser)
    inspect_parser.add_argument("--format", choices=["text", "json", "md"], default="text")
    inspect_parser.add_argument("--docs", metavar="FILE", help="write a Markdown reference doc")
    inspect_parser.add_argument("--manifest", metavar="FILE", help="write a deterministic manifest")
    inspect_parser.add_argument("--html", metavar="FILE", help="write a self-contained HTML report")
    inspect_parser.add_argument("--fail-under", type=int, metavar="N", dest="fail_under")
    inspect_parser.set_defaults(func=cmd_inspect)

    audit_parser = subparsers.add_parser("audit", help="static security rules over the surface")
    _target_flags(audit_parser)
    audit_parser.add_argument("--format", choices=["text", "json", "md"], default="text")
    audit_parser.add_argument(
        "--fail-on",
        choices=[severity.value for severity in Severity],
        default=Severity.HIGH.value,
        dest="fail_on",
        help="exit 1 when a finding at or above this severity exists (default: high)",
    )
    audit_parser.add_argument("--html", metavar="FILE")
    audit_parser.set_defaults(func=cmd_audit)

    conf_parser = subparsers.add_parser("conformance", help="run the protocol conformance suite")
    _target_flags(conf_parser)
    conf_parser.add_argument("--format", choices=["text", "json", "md"], default="text")
    conf_parser.add_argument("--html", metavar="FILE")
    conf_parser.add_argument("--fail-under", type=int, metavar="N", dest="fail_under")
    conf_parser.set_defaults(func=cmd_conformance)

    tool_parser = subparsers.add_parser("test-tool", help="call one tool and validate the result")
    _target_flags(tool_parser)
    tool_parser.add_argument("--tool", metavar="NAME", help="tool name to call")
    tool_parser.add_argument(
        "--args", metavar="JSON", help="JSON object of arguments, or @path/to/args.json"
    )
    tool_parser.add_argument("--format", choices=["text", "json"], default="text")
    tool_parser.set_defaults(func=cmd_test_tool)

    discover_parser = subparsers.add_parser(
        "discover", help="scan MCP client config files for broken entries"
    )
    discover_parser.add_argument(
        "--config", action="append", metavar="PATH", help="extra config file to scan"
    )
    discover_parser.add_argument("--format", choices=["text", "json"], default="text")
    discover_parser.add_argument("--no-color", action="store_true")
    discover_parser.set_defaults(func=cmd_discover)

    report_parser = subparsers.add_parser("report", help="write a self-contained HTML report")
    _target_flags(report_parser)
    report_parser.add_argument("--out", metavar="FILE", help="output path")
    report_parser.add_argument("--format", choices=["text", "json"], default="text")
    report_parser.set_defaults(func=cmd_report)

    init_parser = subparsers.add_parser("init", help="write a demo stdio MCP server")
    init_parser.add_argument(
        "directory",
        nargs="?",
        default="demo_server",
        help="target directory (default: demo_server)",
    )
    init_parser.set_defaults(func=cmd_init)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args))
    except UsageError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2
    except TransportError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1
    except McpDoctorError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0
