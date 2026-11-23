"""Shared fixtures: paths to the bundled demo servers and a tiny MCP test server."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEMO_SERVER = ROOT / "examples" / "demo_server" / "server.py"
BUGGY_SERVER = ROOT / "examples" / "buggy_server" / "server.py"


@pytest.fixture(scope="session")
def demo_command() -> str:
    """The --stdio command for the bundled demo server."""
    return '"' + sys.executable + '" "' + str(DEMO_SERVER) + '"'


@pytest.fixture(scope="session")
def buggy_command() -> str:
    """The --stdio command for the deliberately insecure server."""
    return '"' + sys.executable + '" "' + str(BUGGY_SERVER) + '"'
