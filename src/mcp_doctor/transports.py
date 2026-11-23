"""Transports: newline-delimited stdio and Streamable HTTP, both stdlib only.

Two implementations, one tiny interface:

  * StdioTransport spawns the server as a child process and exchanges
    newline-delimited JSON-RPC frames on its stdin/stdout, exactly as the MCP
    stdio transport specifies. A reader thread drains stdout so a slow server
    can never block the caller, and stderr is captured because that is where
    MCP servers print the reason they died.
  * HttpTransport speaks the Streamable HTTP transport with urllib: POST a
    JSON body, accept either an application/json body or a text/event-stream
    reply, and carry the Mcp-Session-Id and MCP-Protocol-Version headers.

No SDK, no aiohttp, no httpx. That is the point: mcp-doctor must be able to
test a server in an environment where nothing but Python is installed.
"""

from __future__ import annotations

import os
import queue
import shlex
import subprocess
import threading
import urllib.error
import urllib.request
from collections import deque
from typing import Protocol

from .errors import TransportError

DEFAULT_TIMEOUT = 15.0


class Transport(Protocol):
    """The only interface Peer needs from a transport."""

    def send_raw(self, text: str) -> None:
        """Send one already-serialised JSON-RPC frame."""

    def receive(self, timeout: float) -> str:
        """Return the next raw frame, or raise TransportError on timeout."""

    def close(self) -> None:
        """Release the process or connection."""

    def describe(self) -> str:
        """Short human label such as stdio: python server.py."""


class StdioTransport:
    """Newline-delimited JSON-RPC over a spawned child process."""

    def __init__(self, command: str, cwd: str | None = None) -> None:
        self.command = command
        self.cwd = cwd
        self.stderr_lines: deque[str] = deque(maxlen=400)
        self._closed = False
        argv = self._build_argv(command)
        try:
            self.process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                bufsize=0,
                shell=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
            )
        except FileNotFoundError as exc:
            raise TransportError(
                f"could not start server: executable not found ({argv[0]!r}). "
                "Check the --stdio command."
            ) from exc
        except OSError as exc:
            raise TransportError(f"could not start server: {exc}") from exc
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(
            target=self._read_loop, name="mcp-doctor-reader", daemon=True
        )
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr, name="mcp-doctor-stderr", daemon=True
        )
        self._stderr_reader.start()

    @staticmethod
    def _build_argv(command: str) -> list[str]:
        """Split a shell-ish command line without invoking a shell.

        On Windows a naive posix split would eat backslashes in paths such as
        C:/Python/python.exe, so the platform default is used there and any
        residual surrounding quotes are stripped.
        """
        args = shlex.split(command, posix=(os.name != "nt"))
        return [arg[1:-1] if len(arg) > 1 and arg[0] == arg[-1] == '"' else arg for arg in args]

    def _read_loop(self) -> None:
        stream = self.process.stdout
        if stream is None:
            self._queue.put(None)
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._queue.put(line)
        finally:
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
            self._queue.put(None)

    def _drain_stderr(self) -> None:
        stream = self.process.stderr
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            self.stderr_lines.append(raw.decode("utf-8", "replace").rstrip())
        stream.close()

    def send_raw(self, text: str) -> None:
        if self._closed:
            raise TransportError("transport is closed")
        if self.process.poll() is not None:
            raise TransportError(self._death_message())
        stream = self.process.stdin
        if stream is None:
            raise TransportError("server stdin is not available")
        try:
            stream.write((text + "\n").encode("utf-8"))
            stream.flush()
        except (BrokenPipeError, OSError) as exc:
            raise TransportError(self._death_message() + f" ({exc})") from exc

    def receive(self, timeout: float) -> str:
        try:
            item = self._queue.get(timeout=max(timeout, 0.001))
        except queue.Empty:
            raise TransportError(f"no response within {timeout:.1f}s") from None
        if item is None:
            raise TransportError(self._death_message())
        return item

    def _death_message(self) -> str:
        code = self.process.poll()
        tail = [line for line in self.stderr_lines if line.strip()][-6:]
        detail = ("; stderr: " + " | ".join(tail)) if tail else ""
        if code is None:
            return "server closed its stdout" + detail
        return f"server exited with code {code}" + detail

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self.process
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    def describe(self) -> str:
        return "stdio: " + self.command


# --------------------------------------------------------------------------- #
# Streamable HTTP
# --------------------------------------------------------------------------- #


class HttpTransport:
    """Streamable HTTP transport using urllib only.

    Each JSON-RPC message is POSTed to the endpoint. A reply may come back as
    application/json or as a text/event-stream whose data lines carry JSON-RPC
    frames; both are handled. The Mcp-Session-Id handed out during initialize
    is echoed on every later request, as the spec requires.
    """

    def __init__(self, url: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._pending: deque[str] = deque()
        self.last_headers: dict[str, str] = {}
        self.last_status: int = 0
        self._closed = False

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": accept,
            "User-Agent": "mcp-doctor/1.0",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def post(self, text: str, accept: str | None = None) -> tuple[int, str, dict[str, str]]:
        """POST one frame and return (status, body, headers).

        Raises TransportError when the endpoint cannot be reached at all.
        """
        if self._closed:
            raise TransportError("transport is closed")
        payload = text.encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers=self._headers(accept or "application/json, text/event-stream"),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
                headers = {k.lower(): v for k, v in response.headers.items()}
                self.last_status = response.status
                self.last_headers = headers
                session = headers.get("mcp-session-id")
                if session:
                    self.session_id = session
                return response.status, body, headers
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace") if exc.fp else ""
            headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
            self.last_status = exc.code
            self.last_headers = headers
            return exc.code, body, headers
        except urllib.error.URLError as exc:
            raise TransportError(f"cannot reach {self.url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TransportError(
                f"timed out after {self.timeout:.0f}s talking to {self.url}"
            ) from exc

    def send_raw(self, text: str) -> None:
        if self._closed:
            raise TransportError("transport is closed")
        status, body, headers = self.post(text)
        if status >= 400 and not body:
            raise TransportError(f"HTTP {status} from {self.url}")
        for frame in self._extract_frames(body, headers):
            self._pending.append(frame)

    def _extract_frames(self, body: str, headers: dict[str, str]) -> list[str]:
        content_type = headers.get("content-type", "")
        body = body.strip()
        if not body:
            return []
        if "text/event-stream" in content_type:
            frames: list[str] = []
            data_lines: list[str] = []
            for line in body.splitlines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif not line.strip() and data_lines:
                    frames.append("\n".join(data_lines))
                    data_lines = []
            if data_lines:
                frames.append("\n".join(data_lines))
            return frames
        return [body]

    def receive(self, timeout: float) -> str:
        if self._pending:
            return self._pending.popleft()
        raise TransportError(
            "no response body was returned (the server may stream results on a "
            "separate GET connection, which mcp-doctor does not consume)"
        )

    def close(self) -> None:
        self._closed = True

    def describe(self) -> str:
        return "http: " + self.url


def build_transport(
    stdio: str | None, http: str | None, timeout: float = DEFAULT_TIMEOUT
) -> Transport:
    """Create the transport described by the CLI target flags."""
    if bool(stdio) == bool(http):
        raise TransportError("exactly one of --stdio or --http is required")
    if stdio:
        return StdioTransport(stdio)
    assert http is not None
    if not http.startswith(("http://", "https://")):
        raise TransportError("--http must be an http:// or https:// URL")
    return HttpTransport(http, timeout=timeout)
