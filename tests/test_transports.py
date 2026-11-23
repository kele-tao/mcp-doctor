"""Transport tests: real subprocesses and a real stdlib HTTP server, no mocks."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mcp_doctor.errors import TransportError
from mcp_doctor.transports import HttpTransport, StdioTransport, build_transport

ECHO_SERVER = """
import sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    sys.stdout.write(line + chr(10))
    sys.stdout.flush()
"""


@pytest.fixture()
def echo_server(tmp_path):
    path = tmp_path / "echo_server.py"
    path.write_text(ECHO_SERVER, encoding="utf-8")
    return '"' + sys.executable + '" "' + str(path) + '"'


def test_stdio_round_trip(echo_server):
    transport = StdioTransport(echo_server)
    try:
        transport.send_raw('{"jsonrpc": "2.0", "id": 1, "method": "ping"}')
        frame = transport.receive(5.0)
        assert json.loads(frame)["method"] == "ping"
    finally:
        transport.close()
    assert transport.describe().startswith("stdio: ")


def test_stdio_close_is_idempotent(echo_server):
    transport = StdioTransport(echo_server)
    transport.close()
    transport.close()
    with pytest.raises(TransportError):
        transport.send_raw("{}")


def test_stdio_missing_executable():
    with pytest.raises(TransportError) as excinfo:
        StdioTransport("definitely-not-a-real-executable-xyz --flag")
    assert "executable not found" in str(excinfo.value)


def test_stdio_death_reports_stderr(tmp_path):
    script = tmp_path / "dies.py"
    script.write_text(
        "import sys\nsys.stderr.write('boom: config missing\\n')\nsys.exit(3)\n", encoding="utf-8"
    )
    command = '"' + sys.executable + '" "' + str(script) + '"'
    transport = StdioTransport(command)
    try:
        with pytest.raises(TransportError) as excinfo:
            transport.receive(5.0)
        message = str(excinfo.value)
        assert "exited with code 3" in message
        assert "config missing" in message
    finally:
        transport.close()


def test_stdio_timeout(echo_server):
    transport = StdioTransport(echo_server)
    try:
        with pytest.raises(TransportError) as excinfo:
            transport.receive(0.05)
        assert "no response within" in str(excinfo.value)
    finally:
        transport.close()


def test_build_transport_requires_exactly_one_target():
    with pytest.raises(TransportError):
        build_transport(None, None)
    with pytest.raises(TransportError):
        build_transport("python x.py", "http://example.com/mcp")
    with pytest.raises(TransportError):
        build_transport(None, "ftp://example.com/mcp")
    assert isinstance(build_transport("python x.py", None), StdioTransport)


class _Handler(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        type(self).received.append(
            {"body": body, "headers": {k.lower(): v for k, v in self.headers.items()}}
        )
        message = json.loads(body)
        if self.path == "/sse":
            frame = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"ok": True}})
            payload = "event: message\n" + "data: " + frame + "\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Mcp-Session-Id", "session-123")
            self.send_header("Content-Length", str(len(payload.encode())))
            self.end_headers()
            self.wfile.write(payload.encode())
            return
        payload = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"ok": True}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload.encode())))
        self.end_headers()
        self.wfile.write(payload.encode())

    def log_message(self, *args):
        pass


@pytest.fixture()
def http_endpoint():
    _Handler.received = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield "http://127.0.0.1:" + str(port), port
    server.shutdown()
    server.server_close()


def test_http_json_round_trip(http_endpoint):
    base, _ = http_endpoint
    transport = HttpTransport(base + "/mcp")
    try:
        transport.send_raw('{"jsonrpc": "2.0", "id": 1, "method": "ping"}')
        frame = json.loads(transport.receive(5.0))
        assert frame["result"] == {"ok": True}
        assert transport.last_status == 200
    finally:
        transport.close()


def test_http_session_header_is_echoed(http_endpoint):
    base, _ = http_endpoint
    transport = HttpTransport(base + "/sse")
    try:
        transport.send_raw('{"jsonrpc": "2.0", "id": 7, "method": "ping"}')
        assert transport.session_id == "session-123"
        transport.send_raw('{"jsonrpc": "2.0", "id": 8, "method": "ping"}')
        assert _Handler.received[-1]["headers"]["mcp-session-id"] == "session-123"
    finally:
        transport.close()


def test_http_sse_frames_are_unwrapped(http_endpoint):
    base, _ = http_endpoint
    transport = HttpTransport(base + "/sse")
    try:
        transport.send_raw('{"jsonrpc": "2.0", "id": 3, "method": "tools/list"}')
        frame = json.loads(transport.receive(5.0))
        assert frame["id"] == 3
        assert frame["result"] == {"ok": True}
    finally:
        transport.close()


def test_http_unreachable_port():
    transport = HttpTransport("http://127.0.0.1:9/mcp", timeout=1.0)
    try:
        with pytest.raises(TransportError) as excinfo:
            transport.send_raw('{"jsonrpc": "2.0", "id": 1, "method": "ping"}')
        assert "cannot reach" in str(excinfo.value)
    finally:
        transport.close()
