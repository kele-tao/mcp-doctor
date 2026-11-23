"""Exception hierarchy shared by the transport, protocol and CLI layers.

The CLI never prints a traceback for any of these: it prints "error: ..." and
exits with the documented code (2 for usage problems, 1 for failed checks, 1
for a transport failure).
"""

from __future__ import annotations


class McpDoctorError(Exception):
    """Base class for every error mcp-doctor raises on purpose."""

    exit_code = 1


class UsageError(McpDoctorError):
    """The user asked for something impossible (bad flag, bad JSON, bad file)."""

    exit_code = 2


class TransportError(McpDoctorError):
    """The server could not be started, reached, or stopped talking."""

    exit_code = 1


class ServerError(McpDoctorError):
    """The server answered with a JSON-RPC error object."""

    def __init__(self, code: int, message: str, data: object | None = None) -> None:
        super().__init__(f"server error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data

    def __str__(self) -> str:
        if self.data is None:
            return f"server error {self.code}: {self.message}"
        return f"server error {self.code}: {self.message} (data={self.data!r})"
