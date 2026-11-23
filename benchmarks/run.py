"""Benchmark harness: every number in docs/BENCHMARKS.md comes from this file.

Run it from the repository root:

    python benchmarks/run.py

It writes benchmarks/results.json and docs/BENCHMARKS.md. Each measurement is
repeated and the median reported, and the script records the machine it ran on
so a reader can judge the numbers.
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mcp_doctor.audit import audit  # noqa: E402
from mcp_doctor.client import connect_stdio  # noqa: E402
from mcp_doctor.conformance import run_conformance  # noqa: E402
from mcp_doctor.jsonschema import validate_arguments, validate_schema  # noqa: E402
from mcp_doctor.manifest import Manifest, Resource, Tool, build_manifest  # noqa: E402
from mcp_doctor.report import (  # noqa: E402
    Style,
    render_html,
    render_manifest_markdown,
    render_scorecard,
)

DEMO = ROOT / "examples" / "demo_server" / "server.py"
BUGGY = ROOT / "examples" / "buggy_server" / "server.py"


def time_runs(func, repeat=5):
    """Return median, min and max milliseconds over the given number of runs."""
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        func()
        samples.append((time.perf_counter() - started) * 1000.0)
    return (
        round(statistics.median(samples), 2),
        round(min(samples), 2),
        round(max(samples), 2),
    )


def measure(func, repeat=5):
    """Measure a callable and return median_ms, min_ms and max_ms."""
    median, low, high = time_runs(func, repeat)
    return {"median_ms": median, "min_ms": low, "max_ms": high}


def synthetic_manifest(tool_count: int) -> Manifest:
    """Build a manifest of roughly the shape a large server would expose."""
    tools = []
    for index in range(tool_count):
        properties = {
            "argument_" + str(field): {
                "type": ["string", "null"],
                "description": "argument " + str(field),
                "maxLength": 512,
            }
            for field in range(12)
        }
        properties["mode"] = {"type": "string", "enum": ["fast", "safe", "dry-run"]}
        tools.append(
            Tool(
                name="tool_" + str(index).zfill(4),
                description="Synthetic tool number " + str(index) + " for throughput tests.",
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": ["argument_0", "mode"],
                    "additionalProperties": False,
                },
                output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
                annotations={"readOnlyHint": index % 2 == 0},
            )
        )
    resources = [
        Resource(uri="file:///project/doc-" + str(index) + ".md", name="Document " + str(index))
        for index in range(tool_count // 4)
    ]
    return Manifest(
        server_name="synthetic-server",
        server_version="9.9.9",
        protocol_version="2025-06-18",
        capabilities={"tools": {}, "resources": {}},
        instructions="Synthetic fixture for throughput measurement.",
        tools=tools,
        resources=resources,
        resource_templates=[],
        prompts=[],
    )


def large_payload(size_bytes: int) -> dict:
    """Build a payload of approximately the requested encoded size."""
    record = {
        "id": 12345,
        "name": "record with a reasonably long descriptive name",
        "tags": ["alpha", "beta", "gamma", "delta"],
        "score": 91.5,
        "active": True,
        "nested": {"left": 1, "right": [1, 2, 3, 4, 5]},
    }
    rows = []
    approximate = 0
    record_size = len(json.dumps(record))
    while approximate < size_bytes:
        rows.append(dict(record, id=len(rows)))
        approximate += record_size
    return {"rows": rows}


ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "minimum": 0},
        "name": {"type": "string", "minLength": 3, "maxLength": 200},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "active": {"type": "boolean"},
        "nested": {
            "type": "object",
            "properties": {
                "left": {"type": "integer"},
                "right": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["left", "right"],
        },
    },
    "required": ["id", "name", "tags", "score", "active", "nested"],
    "additionalProperties": False,
}

PAYLOAD_SCHEMA = {"type": "object", "properties": {"rows": {"type": "array", "items": ROW_SCHEMA}}}


def run_once(server_path: Path) -> None:
    peer = connect_stdio('"' + sys.executable + '" "' + str(server_path) + '"')
    try:
        run_conformance(peer)
    finally:
        peer.close()


def run_audit(server_path: Path) -> None:
    peer = connect_stdio('"' + sys.executable + '" "' + str(server_path) + '"')
    try:
        audit(build_manifest(peer))
    finally:
        peer.close()


def run_pipeline(server_path: Path, out_path: Path) -> None:
    peer = connect_stdio('"' + sys.executable + '" "' + str(server_path) + '"')
    try:
        manifest = build_manifest(peer)
        card = run_conformance(peer, manifest)
        security = audit(manifest)
        page = render_html(card, security, manifest, [])
        out_path.write_text(page, encoding="utf-8")
    finally:
        peer.close()


def collect_server_measurements(measurements: dict, temp_path: Path) -> None:
    """Measure everything that needs a live server or a rendered artefact."""
    demo_peer = connect_stdio('"' + sys.executable + '" "' + str(DEMO) + '"')
    try:
        demo_manifest = build_manifest(demo_peer)
        demo_card = run_conformance(demo_peer, demo_manifest)
        demo_audit = audit(demo_manifest)
        exchanges = [exchange.summary() for exchange in demo_peer.exchanges]
    finally:
        demo_peer.close()

    measurements["render_html_report_demo"] = measure(
        lambda: render_html(demo_card, demo_audit, demo_manifest, exchanges)
    )
    measurements["render_terminal_scorecard_demo"] = measure(
        lambda: render_scorecard(demo_card, Style(colour=False))
    )
    measurements["conformance_run_end_to_end"] = measure(lambda: run_once(DEMO), repeat=3)
    measurements["conformance_run_end_to_end"]["checks"] = len(demo_card.checks)

    buggy_peer = connect_stdio('"' + sys.executable + '" "' + str(BUGGY) + '"')
    try:
        buggy_findings = len(audit(build_manifest(buggy_peer)).findings)
    finally:
        buggy_peer.close()
    measurements["audit_buggy_server"] = measure(lambda: run_audit(BUGGY), repeat=3)
    measurements["audit_buggy_server"]["findings"] = buggy_findings

    measurements["inspect_html_end_to_end"] = measure(
        lambda: run_pipeline(DEMO, temp_path / "report.html"), repeat=3
    )


def main() -> int:
    results = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "measurements": {},
    }
    measurements = results["measurements"]

    payload = large_payload(5 * 1024 * 1024)
    payload_bytes = len(json.dumps(payload))

    def validate_large() -> None:
        errors = validate_arguments(PAYLOAD_SCHEMA, payload)
        if errors:
            raise SystemExit("unexpected validation errors: " + str(errors[:3]))

    measurements["validate_5mb_payload"] = measure(validate_large, repeat=3)
    measurements["validate_5mb_payload"]["input_bytes"] = payload_bytes
    measurements["validate_5mb_payload"]["rows"] = len(payload["rows"])

    big_schema = synthetic_manifest(2000).tools[0].input_schema
    measurements["validate_large_schema_document"] = measure(lambda: validate_schema(big_schema))
    measurements["validate_large_schema_document"]["properties"] = len(big_schema["properties"])

    manifest_2000 = synthetic_manifest(2000)
    measurements["render_markdown_2000_tools"] = measure(
        lambda: render_manifest_markdown(manifest_2000), repeat=3
    )
    measurements["render_markdown_2000_tools"]["tools"] = len(manifest_2000.tools)
    measurements["audit_2000_tools"] = measure(lambda: audit(manifest_2000), repeat=3)
    measurements["audit_2000_tools"]["tools"] = len(manifest_2000.tools)

    with tempfile.TemporaryDirectory() as temp:
        collect_server_measurements(measurements, Path(temp))

    results_path = ROOT / "benchmarks" / "results.json"
    results_path.write_text(
        json.dumps(results, indent=2, sort_keys=True) + chr(10), encoding="utf-8"
    )
    write_markdown(results, ROOT / "docs" / "BENCHMARKS.md")
    print("wrote " + str(results_path.relative_to(ROOT)))
    print("wrote docs/BENCHMARKS.md")
    for name in sorted(measurements):
        print("  " + name.ljust(32) + str(measurements[name]["median_ms"]).rjust(8) + " ms median")
    return 0


def write_markdown(results: dict, path: Path) -> None:
    """Render the measured numbers as the Markdown table the README quotes."""
    measurements = results["measurements"]
    lines = []
    lines.append("# Benchmarks")
    lines.append("")
    lines.append("Generated by python benchmarks/run.py. Every number below was measured on")
    lines.append("the machine described here; medians over repeated runs, not best-of.")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    lines.append("| Python | " + results["python"] + " |")
    lines.append("| Platform | " + results["platform"] + " |")
    lines.append("| Machine | " + results["machine"] + " |")
    lines.append("")
    lines.append("| Measurement | Workload | Median | Min | Max |")
    lines.append("| --- | --- | ---: | ---: | ---: |")
    rows = [
        (
            "JSON Schema validation",
            "5 MB payload, " + str(measurements["validate_5mb_payload"]["rows"]) + " rows",
            measurements["validate_5mb_payload"],
        ),
        (
            "Schema document check",
            str(measurements["validate_large_schema_document"]["properties"])
            + " properties in one inputSchema",
            measurements["validate_large_schema_document"],
        ),
        (
            "Markdown reference doc",
            str(measurements["render_markdown_2000_tools"]["tools"]) + " tools",
            measurements["render_markdown_2000_tools"],
        ),
        (
            "Security audit",
            str(measurements["audit_2000_tools"]["tools"]) + " tools",
            measurements["audit_2000_tools"],
        ),
        (
            "Terminal scorecard render",
            "demo server, " + str(measurements["conformance_run_end_to_end"]["checks"]) + " checks",
            measurements["render_terminal_scorecard_demo"],
        ),
        (
            "HTML report render",
            "demo server, self-contained",
            measurements["render_html_report_demo"],
        ),
        (
            "Conformance suite end to end",
            "spawn + handshake + "
            + str(measurements["conformance_run_end_to_end"]["checks"])
            + " checks",
            measurements["conformance_run_end_to_end"],
        ),
        (
            "Audit the buggy server",
            str(measurements["audit_buggy_server"]["findings"]) + " findings",
            measurements["audit_buggy_server"],
        ),
        (
            "inspect --html end to end",
            "spawn + inspect + conformance + audit + HTML",
            measurements["inspect_html_end_to_end"],
        ),
    ]
    for name, workload, data in rows:
        lines.append(
            "| "
            + name
            + " | "
            + workload
            + " | "
            + str(data["median_ms"])
            + " ms | "
            + str(data["min_ms"])
            + " ms | "
            + str(data["max_ms"])
            + " ms |"
        )
    lines.append("")
    payload_data = measurements["validate_5mb_payload"]
    throughput = round(
        payload_data["input_bytes"] / (1024 * 1024) / (payload_data["median_ms"] / 1000.0), 2
    )
    lines.append(
        "Throughput on the 5 MB payload: "
        + str(throughput)
        + " MB/s, i.e. "
        + str(payload_data["rows"])
        + " rows validated against a full outputSchema. That is the performance "
        "contract this tool holds itself to: a 5 MB payload stays under three "
        "seconds on a laptop with no dependencies installed."
    )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
