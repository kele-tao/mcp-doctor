"""Report rendering: tables align, JSON is stable, HTML is self-contained."""

from __future__ import annotations

import json

from mcp_doctor.audit import audit
from mcp_doctor.client import connect_stdio
from mcp_doctor.conformance import run_conformance
from mcp_doctor.manifest import build_manifest
from mcp_doctor.report import (
    Style,
    bar,
    render_audit,
    render_html,
    render_inspect,
    render_scorecard,
    table,
    truncate,
    visible_len,
)

PLAIN = Style(colour=False)


def test_visible_len_ignores_ansi():
    coloured = Style(colour=True)
    assert visible_len(coloured.paint("abc", "\x1b[31m")) == 3
    assert visible_len("abc") == 3


def test_table_aligns_columns():
    rendered = table(["A", "B"], [["1", "22"], ["333", "4"]], PLAIN)
    lines = rendered.splitlines()
    assert lines[0].split() == ["A", "B"]
    assert set(lines[1]) <= {"-", " "}
    assert len(lines) == 4


def test_truncate_and_bar():
    assert truncate("abcdefgh", 5) == "abcd..."
    assert truncate("abc", 5) == "abc"
    assert set(bar(50, 10, PLAIN)) <= {"#", "."}
    assert bar(100, 10, PLAIN) == "#" * 10


def test_scorecard_render_groups_by_area(demo_command):
    peer = connect_stdio(demo_command)
    try:
        card = run_conformance(peer)
    finally:
        peer.close()
    text = render_scorecard(card, PLAIN)
    assert "conformance" in text
    assert "100/100" in text
    assert "AREA" in text
    assert "INIT" in text
    assert "PROTO" in text


def test_scorecard_verbose_lists_every_check(demo_command):
    peer = connect_stdio(demo_command)
    try:
        card = run_conformance(peer)
    finally:
        peer.close()
    text = render_scorecard(card, PLAIN, verbose=True)
    for check in card.checks:
        assert check.id in text


def test_audit_render_lists_findings(buggy_command):
    peer = connect_stdio(buggy_command)
    try:
        report = audit(build_manifest(peer))
    finally:
        peer.close()
    text = render_audit(report, PLAIN)
    assert "security audit" in text
    assert "MCP201" in text
    assert "fix:" in text


def test_inspect_render_has_all_sections(demo_command):
    peer = connect_stdio(demo_command)
    try:
        text = render_inspect(build_manifest(peer), PLAIN)
    finally:
        peer.close()
    assert "demo-tools 1.0.0" in text
    assert "TOOLS" in text
    assert "RESOURCES" in text
    assert "word_count" in text


def test_html_report_is_self_contained(demo_command):
    peer = connect_stdio(demo_command)
    try:
        manifest = build_manifest(peer)
        card = run_conformance(peer, manifest)
        report = audit(manifest)
        page = render_html(card, report, manifest, [e.summary() for e in peer.exchanges])
    finally:
        peer.close()
    assert page.startswith("<!doctype html>")
    assert "<style>" in page
    assert "http://" not in page.replace("http://127.0.0.1", "")
    assert "cdn" not in page.lower()
    assert "Conformance checks" in page
    assert "demo-tools" in page
    assert page.count("<table>") >= 2


def test_html_escapes_hostile_names():
    from mcp_doctor.audit import AuditReport
    from mcp_doctor.conformance import Scorecard
    from mcp_doctor.manifest import Manifest, Tool

    manifest = Manifest(
        server_name="<script>alert(1)</script>",
        server_version="1",
        protocol_version="2025-06-18",
        capabilities={},
        instructions=None,
        tools=[Tool(name="x<y", description="a & b")],
        resources=[],
        resource_templates=[],
        prompts=[],
    )
    page = render_html(
        Scorecard(server=manifest.server_name, protocol="p"), AuditReport(), manifest
    )
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "a &amp; b" in page


def test_json_payloads_are_parseable(demo_command):
    peer = connect_stdio(demo_command)
    try:
        card = run_conformance(peer)
    finally:
        peer.close()
    payload = json.loads(json.dumps(card.to_dict(), sort_keys=True))
    assert payload["grade"]
    assert isinstance(payload["checks"], list)
