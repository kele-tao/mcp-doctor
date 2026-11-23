"""CLI tests: exit codes, --format json, file outputs and graceful failures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mcp_doctor.cli import build_parser, main

ROOT = Path(__file__).resolve().parents[1]


def run(argv):
    try:
        return main(argv)
    except SystemExit as exc:  # argparse paths
        return int(exc.code or 0)


def test_no_command_prints_help_and_returns_2(capsys):
    assert run([]) == 2
    captured = capsys.readouterr()
    assert "COMMAND" in captured.out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "mcp-doctor 1.0.0" in capsys.readouterr().out


def test_parser_lists_every_documented_command():
    parser = build_parser()
    actions = [action for action in parser._actions if isinstance(action, type(parser._subparsers))]
    del actions
    text = parser.format_help()
    for command in ("inspect", "audit", "conformance", "test-tool", "discover", "report", "init"):
        assert command in text


def test_missing_target_is_a_usage_error(capsys):
    assert run(["inspect"]) == 2
    assert "give a server to talk to" in capsys.readouterr().err


def test_both_targets_is_a_usage_error(capsys):
    code = run(["audit", "--stdio", "python x.py", "--http", "http://x/mcp"])
    assert code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_bad_timeout_is_a_usage_error(capsys):
    assert run(["audit", "--stdio", "python x.py", "--timeout", "0"]) == 2
    assert "--timeout" in capsys.readouterr().err


def test_unknown_executable_exits_1_with_hint(capsys):
    code = run(["inspect", "--stdio", "definitely-not-a-real-binary-zzz"])
    assert code == 1
    error = capsys.readouterr().err
    assert "executable not found" in error
    assert "hint:" in error


def test_inspect_text_output(demo_command, capsys):
    assert run(["inspect", "--stdio", demo_command, "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "demo-tools 1.0.0" in out
    assert "conformance" in out
    assert "security audit" in out


def test_inspect_json_output(demo_command, capsys):
    assert run(["inspect", "--stdio", demo_command, "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest"]["server"]["name"] == "demo-tools"
    assert payload["conformance"]["score"] == 100
    assert payload["security"]["findings"] == []


def test_inspect_writes_docs_manifest_and_html(demo_command, tmp_path, capsys):
    docs = tmp_path / "reference.md"
    manifest = tmp_path / "manifest.json"
    html = tmp_path / "report.html"
    code = run(
        [
            "inspect",
            "--stdio",
            demo_command,
            "--no-color",
            "--docs",
            str(docs),
            "--manifest",
            str(manifest),
            "--html",
            str(html),
        ]
    )
    assert code == 0
    assert "## Tools" in docs.read_text(encoding="utf-8")
    assert json.loads(manifest.read_text(encoding="utf-8"))["tools"]
    assert "mcp-doctor report" in html.read_text(encoding="utf-8")


def test_audit_fails_buggy_server(buggy_command, capsys):
    assert run(["audit", "--stdio", buggy_command, "--no-color"]) == 1
    captured = capsys.readouterr()
    assert "MCP201" in captured.out
    assert "failing the run" in captured.err


def test_audit_threshold_controls_exit_code(buggy_command, capsys):
    code = run(["audit", "--stdio", buggy_command, "--no-color", "--fail-on", "critical"])
    assert code == 1
    capsys.readouterr()


def test_audit_json_format(buggy_command, capsys):
    assert run(["audit", "--stdio", buggy_command, "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["score"] == 0
    assert payload["grade"] == "F"
    rules = {finding["rule"] for finding in payload["findings"]}
    assert "MCP201" in rules and "MCP207" in rules


def test_audit_markdown_format(buggy_command, capsys):
    assert run(["audit", "--stdio", buggy_command, "--format", "md"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("# Security audit")
    assert "| Rule | Severity |" in out


def test_conformance_exit_zero_on_demo(demo_command, capsys):
    assert run(["conformance", "--stdio", demo_command, "--no-color"]) == 0
    assert "100/100" in capsys.readouterr().out


def test_conformance_fail_under(demo_command, capsys):
    code = run(["conformance", "--stdio", demo_command, "--no-color", "--fail-under", "101"])
    assert code == 1
    assert "below --fail-under" in capsys.readouterr().err


def test_test_tool_pass_and_fail(demo_command, capsys):
    assert (
        run(["test-tool", "--stdio", demo_command, "--tool", "add", "--args", '{"a": 1, "b": 2}'])
        == 0
    )
    assert "PASS  test-tool add" in capsys.readouterr().out
    code = run(
        ["test-tool", "--stdio", demo_command, "--tool", "add", "--args", '{"a": "x", "b": 2}']
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "expected integer, got string" in out
    assert "FAIL  test-tool add" in out


def test_test_tool_unknown_tool_is_usage_error(demo_command, capsys):
    assert run(["test-tool", "--stdio", demo_command, "--tool", "nope"]) == 2
    assert "available" in capsys.readouterr().err


def test_test_tool_bad_json_is_usage_error(demo_command, capsys):
    assert run(["test-tool", "--stdio", demo_command, "--tool", "add", "--args", "{bad"]) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_test_tool_json_output(demo_command, capsys):
    assert (
        run(
            [
                "test-tool",
                "--stdio",
                demo_command,
                "--tool",
                "word_count",
                "--args",
                '{"text": "one two three"}',
                "--format",
                "json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["structuredContent"] == {"words": 3, "lines": 1}


def test_report_command_writes_file(demo_command, tmp_path, capsys):
    out = tmp_path / "nested" / "report.html"
    assert run(["report", "--stdio", demo_command, "--out", str(out)]) == 0
    assert out.is_file()
    assert "mcp-doctor report" in out.read_text(encoding="utf-8")


def test_discover_finds_broken_entries(tmp_path, capsys, monkeypatch):
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "good": {"command": sys.executable, "args": ["-c", "print(1)"]},
                    "missing": {"command": "no-such-binary-anywhere-zzz"},
                    "remote": {"url": "http://127.0.0.1:9000/mcp"},
                    "empty": {},
                }
            }
        ),
        encoding="utf-8",
    )
    assert run(["discover", "--config", str(config), "--no-color"]) == 1
    out = capsys.readouterr().out
    assert "broken" in out
    assert "no-such-binary-anywhere-zzz" in out


def test_discover_json_and_error_paths(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    assert run(["discover", "--config", str(bad), "--config", str(empty), "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert any("invalid JSON" in error for error in payload["errors"])
    assert any("no mcpServers block" in error for error in payload["errors"])


def test_init_writes_runnable_server(tmp_path, capsys):
    target = tmp_path / "demo"
    assert run(["init", str(target)]) == 0
    server = target / "server.py"
    assert server.is_file()
    source = server.read_text(encoding="utf-8")
    compile(source, str(server), "exec")
    assert "tools/list" in source
    assert "inspect --stdio" in capsys.readouterr().out
