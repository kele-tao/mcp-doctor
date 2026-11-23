"""Conformance suite tests against the bundled demo and buggy servers."""

from __future__ import annotations

from mcp_doctor.client import connect_stdio
from mcp_doctor.conformance import FAIL, PASS, SKIP, Check, Scorecard, run_conformance
from mcp_doctor.manifest import build_manifest


def test_demo_server_passes_everything_applicable(demo_command):
    peer = connect_stdio(demo_command)
    try:
        card = run_conformance(peer)
    finally:
        peer.close()
    assert card.server.startswith("demo-tools")
    assert card.failures() == []
    counts = card.counts()
    assert counts[PASS] >= 20
    assert counts[SKIP] >= 1
    assert card.score >= 95
    assert card.grade() in ("A", "B")
    # Every check has an id and a name; nothing is silently dropped.
    assert len(card.checks) == 25
    assert len({check.id for check in card.checks}) == 25
    for check in card.checks:
        assert check.name
        assert check.status in (PASS, FAIL, "WARN", SKIP)


def test_protocol_checks_are_real(demo_command):
    peer = connect_stdio(demo_command)
    try:
        card = run_conformance(peer)
    finally:
        peer.close()
    by_id = {check.id: check for check in card.checks}
    assert by_id["INIT-01"].detail == "negotiated 2025-06-18"
    assert by_id["PROTO-02"].status == PASS
    assert by_id["PROTO-05"].status == PASS
    assert by_id["PROTO-06"].status == PASS
    assert "tools" in by_id["INIT-04"].detail


def test_buggy_server_scores_lower(buggy_command):
    peer = connect_stdio(buggy_command)
    try:
        manifest = build_manifest(peer)
        card = run_conformance(peer, manifest)
    finally:
        peer.close()
    assert card.score < 100
    failures = {check.id for check in card.failures()}
    assert "CAP-02" in failures, "the buggy server never advertises what it serves"


def test_manifest_records_undeclared_capabilities(buggy_command):
    peer = connect_stdio(buggy_command)
    try:
        manifest = build_manifest(peer)
    finally:
        peer.close()
    assert manifest.tools, "undeclared tools/list must still be probed"
    assert any("undeclared capabilities" in warning for warning in manifest.warnings)


def test_scorecard_arithmetic():
    card = Scorecard(server="s", protocol="p")
    card.add(Check("A", "pass", PASS, weight=2))
    card.add(Check("B", "skipped", SKIP, weight=5))
    assert card.score == 100
    card.add(Check("C", "warn", "WARN", weight=2))
    assert card.score == 75
    card.add(Check("D", "fail", FAIL, weight=1))
    assert card.score == 60
    assert card.grade() == "D"
    assert len(card.failures()) == 1
    payload = card.to_dict()
    assert payload["counts"][PASS] == 1


def test_unreachable_server_still_produces_checks(demo_command, monkeypatch):
    peer = connect_stdio(demo_command)
    try:
        card = run_conformance(peer, build_manifest(peer))
        assert card.checks
    finally:
        peer.close()
