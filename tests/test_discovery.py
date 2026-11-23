"""Config discovery tests, including shape validation and env handling."""

from __future__ import annotations

import json
import sys

from mcp_doctor.discovery import discover, render_discovery
from mcp_doctor.report import Style


def write_config(tmp_path, payload, name=".mcp.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_valid_stdio_and_http_entries(tmp_path):
    config = write_config(
        tmp_path,
        {
            "mcpServers": {
                "local": {"command": sys.executable, "args": ["server.py"]},
                "remote": {"url": "https://example.com/mcp", "type": "http"},
            }
        },
    )
    report = discover([str(config)])
    assert report.counts() == {"OK": 2, "WARN": 0, "BAD": 0}
    assert report.broken == []
    assert report.entries[0].name == "local"


def test_broken_entries_are_flagged(tmp_path):
    config = write_config(
        tmp_path,
        {
            "mcpServers": {
                "no-command": {},
                "bad-url": {"url": "ftp://example.com"},
                "bad-args": {"command": sys.executable, "args": "server.py"},
                "both": {"command": sys.executable, "url": "https://x/mcp"},
                "both-command-issue": {"command": sys.executable, "url": "https://x/mcp"},
                "bad-transport": {"command": sys.executable, "type": "carrier-pigeon"},
                "empty-env": {"command": sys.executable, "env": {"TOKEN": ""}},
            }
        },
    )
    report = discover([str(config)])
    assert len(report.broken) >= 6
    problems = " ".join(" ".join(entry.problems) for entry in report.broken)
    assert "neither command nor url" in problems
    assert "http:// or https://" in problems
    assert "array of strings" in problems
    assert "both command and url" in problems
    assert "unknown transport" in problems
    assert "empty" in problems


def test_unknown_fields_are_warnings(tmp_path):
    config = write_config(
        tmp_path, {"mcpServers": {"x": {"command": sys.executable, "mystery": True}}}
    )
    report = discover([str(config)])
    assert report.entries[0].status == "WARN"
    assert report.entries[0].unknown_fields == ["mystery"]


def test_extension_fields_are_allowed(tmp_path):
    config = write_config(
        tmp_path, {"mcpServers": {"x": {"command": sys.executable, "x-team": "infra"}}}
    )
    report = discover([str(config)])
    assert report.entries[0].status == "OK"


def test_invalid_json_and_missing_block(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("[", encoding="utf-8")
    report = discover([str(broken)])
    assert report.configs == []
    assert "invalid JSON" in report.errors[0]
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"other": 1}), encoding="utf-8")
    report = discover([str(empty)])
    assert "no mcpServers block" in report.errors[0]


def test_servers_alias_and_flat_document(tmp_path):
    aliased = write_config(
        tmp_path, {"servers": {"x": {"command": sys.executable}}}, name="alias.json"
    )
    report = discover([str(aliased)])
    assert report.entries[0].name == "x"
    flat = write_config(tmp_path, {"command": sys.executable}, name="flat.json")
    report = discover([str(flat)])
    assert report.entries[0].name == "flat"


def test_single_server_object_that_is_not_an_object(tmp_path):
    config = write_config(tmp_path, {"mcpServers": {"x": "nope"}})
    report = discover([str(config)])
    assert any("not an object" in error for error in report.errors)


def test_render_discovery_reports_lookups(tmp_path):
    config = write_config(tmp_path, {"mcpServers": {"x": {"command": sys.executable}}})
    report = discover([str(config)])
    text = render_discovery(report, Style(colour=False))
    assert "discover" in text
    assert "x" in text


def test_render_discovery_with_no_configs(tmp_path):
    report = discover([str(tmp_path / "absent.json")])
    text = render_discovery(report, Style(colour=False))
    assert "no MCP config files found" in text
