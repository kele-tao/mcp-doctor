"""mcp-doctor - diagnose, secure and validate Model Context Protocol servers.

The public API is intentionally small and stable::

    from mcp_doctor import connect_stdio, run_conformance, audit

    peer = connect_stdio("python examples/demo_server/server.py")
    scorecard = run_conformance(peer)
    print(scorecard.score)
    peer.close()
"""

from __future__ import annotations

from .audit import Finding, Severity, audit
from .client import Peer, ServerInfo, connect_http, connect_stdio
from .conformance import Check, Scorecard, run_conformance
from .errors import McpDoctorError, ServerError, TransportError, UsageError
from .manifest import Manifest, Prompt, Resource, Tool, build_manifest
from .version import __version__

__all__ = [
    "Check",
    "Finding",
    "Manifest",
    "McpDoctorError",
    "Peer",
    "Prompt",
    "Resource",
    "Scorecard",
    "ServerError",
    "ServerInfo",
    "Severity",
    "Tool",
    "TransportError",
    "UsageError",
    "__version__",
    "audit",
    "build_manifest",
    "connect_http",
    "connect_stdio",
    "run_conformance",
]
