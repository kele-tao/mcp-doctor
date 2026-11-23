"""discover: find MCP configuration files and report broken or dead servers.

Client configuration files are the least documented part of the MCP ecosystem.
A broken entry usually shows up as "the tool does not work" in a chat app with no
error message, so this command reads every config it can find, validates the
shape, resolves the command that would be spawned, and says which entries are
dead. It never starts a server unless asked with --probe.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAMES = (
    "claude_desktop_config.json",
    ".mcp.json",
    "mcp.json",
    "mcp_config.json",
    "settings.json",
)

CONFIG_DIRS = (
    ".cursor",
    ".vscode",
    ".cline",
    ".continue",
    "Claude",
    "claude",
)

KNOWN_FIELDS = {"command", "args", "env", "url", "headers", "type", "transport", "disabled"}


@dataclass
class ServerEntry:
    """One server declared in a config file."""

    name: str
    source: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    env: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        """BAD when the entry cannot be started, WARN when it is legal but odd."""
        if self.problems:
            return "BAD"
        if self.unknown_fields:
            return "WARN"
        return "OK"

    def argv(self) -> list[str]:
        """The command line that would be spawned."""
        return [self.command, *self.args] if self.command else []

    def to_dict(self) -> dict:
        """JSON-ready form."""
        return {
            "name": self.name,
            "source": self.source,
            "status": self.status,
            "transport": "http" if self.url else "stdio",
            "command": self.command,
            "args": self.args,
            "url": self.url,
            "problems": self.problems,
            "unknownFields": self.unknown_fields,
        }


@dataclass
class DiscoveryReport:
    """Every config file found and every server parsed from them."""

    configs: list[str] = field(default_factory=list)
    entries: list[ServerEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def broken(self) -> list[ServerEntry]:
        """Entries that cannot be started as written."""
        return [entry for entry in self.entries if entry.problems]

    def counts(self) -> dict[str, int]:
        """Entry counts per status."""
        out = {"OK": 0, "WARN": 0, "BAD": 0}
        for entry in self.entries:
            out[entry.status] += 1
        return out

    def to_dict(self) -> dict:
        """JSON-ready form."""
        return {
            "configs": self.configs,
            "errors": self.errors,
            "counts": self.counts(),
            "servers": [entry.to_dict() for entry in self.entries],
        }


def candidate_paths(extra: list[str] | None = None) -> list[Path]:
    """Config paths in a deterministic order: explicit, project, user profile."""
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            found.append(path)

    for path in extra or []:
        add(Path(path))
    add(Path.cwd() / ".mcp.json")
    add(Path.cwd() / "mcp.json")
    for directory in CONFIG_DIRS:
        add(Path.cwd() / directory / "mcp.json")
        add(Path.cwd() / directory / "settings.json")
    home = Path(os.path.expanduser("~"))
    for directory in CONFIG_DIRS:
        add(home / directory / "claude_desktop_config.json")
        add(home / directory / "settings.json")
    appdata = os.environ.get("APPDATA")
    if appdata:
        for product in ("Claude", "claude"):
            add(Path(appdata) / product / "claude_desktop_config.json")
    if os.name != "nt":
        add(home / ".config" / "Claude" / "claude_desktop_config.json")
        add(home / ".config" / "mcp" / "config.json")
    return found


def _parse_entry(name: str, raw: dict, source: str) -> ServerEntry:
    entry = ServerEntry(name=name, source=source)
    entry.unknown_fields = sorted(
        key for key in raw if key not in KNOWN_FIELDS and not key.startswith("x-")
    )
    command = raw.get("command")
    url = raw.get("url")
    if command is not None and not isinstance(command, str):
        entry.problems.append("command must be a string")
    elif command:
        entry.command = command
    args = raw.get("args", [])
    if args is None:
        args = []
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        entry.problems.append("args must be an array of strings")
    else:
        entry.args = args
    if url is not None:
        if not isinstance(url, str):
            entry.problems.append("url must be a string")
        elif not url.startswith(("http://", "https://")):
            entry.problems.append("url must start with http:// or https://")
        else:
            entry.url = url
    env = raw.get("env", {})
    if env is None:
        env = {}
    if not isinstance(env, dict):
        entry.problems.append("env must be an object")
    else:
        entry.env = env
    transport = raw.get("type") or raw.get("transport")
    if transport is not None and transport not in ("stdio", "http", "sse", "streamable-http"):
        entry.problems.append("unknown transport " + repr(transport))
    if not entry.command and not entry.url:
        entry.problems.append("entry has neither command nor url")
    if entry.command and entry.url:
        entry.problems.append("entry declares both command and url")
    if entry.command and shutil.which(entry.command) is None:
        local = entry.command.replace("\\\\", "/")
        if not (Path(local).is_absolute() and Path(local).exists()):
            if not Path(local).exists():
                entry.problems.append(
                    "command " + repr(entry.command) + " is not on PATH and not a file"
                )
    if entry.command and entry.url and entry.command in ("npx", "uvx"):
        entry.problems.append("remote url with a package runner command is ambiguous")
    for key, value in entry.env.items():
        if isinstance(value, str) and value == "":
            entry.problems.append("env " + repr(key) + " is empty; unset it instead")
    if not entry.name.strip():
        entry.problems.append("server has an empty name")
    return entry


def discover(
    config_paths: list[str] | None = None, explicit: list[str] | None = None
) -> DiscoveryReport:
    """Read every candidate config and return a structured report."""
    report = DiscoveryReport()
    paths = [Path(item) for item in config_paths] if config_paths else candidate_paths(explicit)
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            report.errors.append(str(path) + ": " + str(exc))
            continue
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            report.errors.append(
                str(path) + ": invalid JSON (" + exc.msg + " at line " + str(exc.lineno) + ")"
            )
            continue
        servers = {}
        if isinstance(document, dict):
            if isinstance(document.get("mcpServers"), dict):
                servers = document["mcpServers"]
            elif isinstance(document.get("servers"), dict):
                servers = document["servers"]
            elif "command" in document or "url" in document:
                servers = {path.stem: document}
        if not servers:
            report.errors.append(str(path) + ": no mcpServers block found")
            continue
        report.configs.append(str(path))
        for name in sorted(servers):
            raw = servers[name]
            if not isinstance(raw, dict):
                report.errors.append(str(path) + ": server " + repr(name) + " is not an object")
                continue
            report.entries.append(_parse_entry(name, raw, str(path)))
    return report


def render_discovery(report: DiscoveryReport, style) -> str:
    """Render a DiscoveryReport for the terminal."""
    lines: list[str] = []
    counts = report.counts()
    lines.append(style.bold("discover"))
    lines.append(
        str(len(report.configs))
        + " config file(s), "
        + str(len(report.entries))
        + " server(s): "
        + style.paint(str(counts["OK"]) + " ok", "\x1b[32m")
        + ", "
        + style.paint(str(counts["WARN"]) + " warn", "\x1b[33m")
        + ", "
        + style.paint(str(counts["BAD"]) + " broken", "\x1b[31m")
    )
    lines.append("")
    if not report.configs:
        lines.append(style.paint("  no MCP config files found", "\x1b[33m"))
        lines.append(
            style.paint(
                "  looked for: .mcp.json, mcp.json, .cursor/mcp.json, "
                "claude_desktop_config.json (project, home, APPDATA)",
                "\x1b[90m",
            )
        )
    for entry in report.entries:
        colour = (
            "\x1b[31m" if entry.problems else "\x1b[33m" if entry.unknown_fields else "\x1b[32m"
        )
        lines.append(
            "  "
            + style.paint(entry.status.ljust(4), colour)
            + " "
            + entry.name.ljust(22)
            + style.paint(entry.source, "\x1b[90m")
        )
        target = entry.url if entry.url else " ".join(entry.argv())
        if target:
            lines.append(style.paint("       " + target, "\x1b[90m"))
        for problem in entry.problems:
            lines.append(style.paint("       - " + problem, "\x1b[31m"))
        for key in entry.unknown_fields:
            lines.append(style.paint("       - unknown field " + repr(key), "\x1b[33m"))
    for error in report.errors:
        lines.append(style.paint("  ! " + error, "\x1b[33m"))
    return "\n".join(lines)
