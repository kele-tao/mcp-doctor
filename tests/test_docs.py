"""Documentation honesty tests: every number in the README must come from the code.

A README that drifts from the implementation is the same failure as a fake
benchmark, just slower. These tests read the published claims out of README.md
and compare them with the constants, the collected test count and the committed
benchmark results, so any of them going stale turns the suite red.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_doctor.audit import RULES
from mcp_doctor.conformance import CHECKS

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
RESULTS = json.loads((ROOT / "benchmarks" / "results.json").read_text(encoding="utf-8"))


def claim(pattern: str) -> str:
    """Return the first capture group of a pattern in the README, or fail loudly."""
    match = re.search(pattern, README)
    assert match is not None, "README no longer contains a claim matching " + pattern
    return match.group(1)


def test_conformance_check_count_claim_is_true():
    claimed = int(claim(r"\*\*(\d+) conformance checks\*\*"))
    assert claimed == len(CHECKS), (
        "README claims "
        + str(claimed)
        + " conformance checks but the suite registers "
        + str(len(CHECKS))
    )


def test_security_rule_count_claim_is_true():
    claimed = int(claim(r"\*\*(\d+) static security rules\*\*"))
    assert claimed == len(RULES), (
        "README claims " + str(claimed) + " rules but audit.py registers " + str(len(RULES))
    )
    first = claim(r"MCP(\d{3})-MCP(\d{3})".replace("(\\d{3})", "(\\d{3})", 1)) if False else None
    del first
    span = re.search(r"MCP(\d{3})-MCP(\d{3})", README)
    assert span is not None
    low, high = int(span.group(1)), int(span.group(2))
    assert high - low + 1 == len(RULES), "MCP rule id range does not match the rule count"


def test_rule_ids_are_contiguous_mcp2xx():
    numbers = sorted(int(name.split()[0][3:]) for name in RULES)
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers)))


def _collected_test_count() -> int:
    """Count every test the suite defines, including parametrised cases.

    Collection runs in a subprocess because this test cannot count the session it
    is running inside: request.session.testscollected at this point already
    includes this test running, which is circular. --collect-only executes no
    test bodies, so the subprocess is safe and takes about a second.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts="],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise AssertionError("pytest --collect-only failed: " + output[-500:])
    for line in reversed(output.splitlines()):
        match = re.search(r"(\d+) tests? collected", line)
        if match:
            return int(match.group(1))
    raise AssertionError("could not read the collected count from pytest output: " + output[-500:])


def test_test_count_badge_matches_collection():
    claimed = int(claim(r"tests-(\d+)%20passing"))
    collected = _collected_test_count()
    assert claimed == collected, (
        "README badge says "
        + str(claimed)
        + " tests but the suite defines "
        + str(collected)
        + ". Update the badge in README.md to "
        + str(collected)
        + "."
    )


def test_readme_benchmark_table_matches_results_json():
    measurements = RESULTS["measurements"]
    expectations = [
        ("JSON Schema validation", measurements["validate_5mb_payload"]),
        ("Markdown reference doc", measurements["render_markdown_2000_tools"]),
        ("Security audit", measurements["audit_2000_tools"]),
        ("HTML report render", measurements["render_html_report_demo"]),
    ]
    for label, data in expectations:
        row = re.search(r"\| " + re.escape(label) + r" \|[^|]*\| ([0-9.]+) (ms|s) \|", README)
        assert row is not None, "README performance table lost the row: " + label
        value = float(row.group(1))
        if row.group(2) == "s":
            value *= 1000.0
        expected = data["median_ms"]
        tolerance = max(1.0, expected * 0.02)
        assert abs(value - expected) <= tolerance, (
            label
            + ": README says "
            + str(value)
            + " ms, results.json says "
            + str(expected)
            + " ms"
        )


def test_readme_points_at_committed_artifacts():
    for relative in (
        "docs/screenshot.png",
        "docs/report.png",
        "docs/BENCHMARKS.md",
        "docs/samples/audit-buggy.txt",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "LICENSE",
    ):
        assert relative in README, "README does not reference " + relative
        assert (ROOT / relative).is_file(), "referenced file is missing: " + relative


def test_readme_documents_the_exit_codes_and_verdicts():
    for token in ("--fail-on", "--fail-under", "exit", "2", "SKIP"):
        assert token in README, "README lost the documented token: " + token


def test_roadmap_records_the_cut_features():
    roadmap = (ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
    for feature in ("GET stream", "OAuth", "SSE", "sampling"):
        assert feature in roadmap, "ROADMAP does not record the cut feature: " + feature
    assert "Cut from v1.0.0" in roadmap


def test_changelog_and_version_agree():
    from mcp_doctor import __version__

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [" + __version__ + "]" in changelog


@pytest.mark.parametrize("path", ["pyproject.toml", ".github/workflows/ci.yml"])
def test_packaging_and_ci_agree_on_the_console_script(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    if path == "pyproject.toml":
        assert 'mcp-doctor = "mcp_doctor.cli:main"' in text
    else:
        assert "3.10" in text and "3.11" in text and "3.12" in text
        assert "ruff check ." in text
        assert "pytest -q" in text
        assert "python -m mcp_doctor --help" in text


def test_readme_coverage_claim_matches_measurement():
    """The README quotes a coverage total; docs/COVERAGE.md is the measurement."""
    coverage_doc = ROOT / "docs" / "COVERAGE.md"
    assert coverage_doc.is_file(), "docs/COVERAGE.md is missing; run python benchmarks/coverage.py"
    measured = re.search(
        r"\| Line coverage \| ([0-9.]+)% \|", coverage_doc.read_text(encoding="utf-8")
    )
    assert measured is not None, "docs/COVERAGE.md lost the total coverage row"
    claimed = re.search(r"\*\*([0-9.]+)%\*\* of statements in", README)
    assert claimed is not None, "README no longer quotes a coverage total"
    assert claimed.group(1) == measured.group(1), (
        "README claims "
        + claimed.group(1)
        + "% coverage but docs/COVERAGE.md measured "
        + measured.group(1)
        + "%"
    )


def test_coverage_script_is_documented():
    assert "benchmarks/coverage.py" in README
    assert (ROOT / "benchmarks" / "coverage.py").is_file()
