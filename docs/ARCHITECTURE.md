# Architecture

## Module map

| Module | Responsibility |
| --- | --- |
| mcp_doctor.jsonrpc | JSON-RPC 2.0 envelopes, error codes, strict parsing |
| mcp_doctor.transports | stdio (subprocess) and Streamable HTTP (urllib) |
| mcp_doctor.client | Peer: handshake, request correlation, pagination |
| mcp_doctor.manifest | Typed views of tools/resources/prompts, JSON export |
| mcp_doctor.jsonschema | Zero-dependency JSON Schema subset validator |
| mcp_doctor.conformance | The protocol checks and the weighted scorecard |
| mcp_doctor.audit | The static security rules and the audit score |
| mcp_doctor.discovery | MCP client configuration scanning |
| mcp_doctor.toolcheck | The staged tool-call verification |
| mcp_doctor.report | Terminal, Markdown, JSON and HTML rendering |
| mcp_doctor.cli | Argument parsing, exit codes, error translation |
| mcp_doctor.generate | The demo server written by the init command |

## Data flow

Every command follows the same path:

    CLI flags
      -> build_transport()          one of StdioTransport / HttpTransport
      -> Peer.initialize()          the MCP handshake, remembers serverInfo
      -> build_manifest(peer)       capability-gated list calls -> Manifest
      -> run_conformance(peer)      -> Scorecard
      -> audit(manifest)            -> AuditReport
      -> render_*()                 -> terminal / JSON / Markdown / HTML
      -> exit code                  0 ok, 1 findings, 2 usage error

The analysis layer never prints. It returns dataclasses (Check, Scorecard,
Finding, AuditReport, Manifest) and the render layer turns them into text. That
separation is why the HTML report, the JSON output and the terminal view cannot
disagree, and why the test suite can assert on structure instead of on captured
strings.

## Why the transport interface is three methods

Transport is a Protocol with send_raw, receive and close. It is not a
request/response interface on purpose: JSON-RPC lets the server interleave
notifications and responses on the same channel, so correlation belongs above
the transport, in Peer. Keeping framing (newline vs HTTP body) in the transport
and id matching in the peer means the HTTP transport can deliver multiple frames
from one POST body without the peer knowing.

## Failure behaviour

- A check that raises becomes a FAIL with the exception text. One broken area
  never hides the scorecard.
- A transport that dies becomes a TransportError carrying the last six lines of
  the server's stderr, so "server exited with code 3; stderr: boom: config
  missing" replaces "no response".
- A user error (bad --args JSON, unknown tool, both --stdio and --http) becomes
  a UsageError and exit code 2, never a traceback.
- A stale response from an earlier probe is recorded as a warning and skipped,
  because a slow server legitimately answers out of order.

## Determinism

There is no randomness anywhere. Manifest JSON sorts its keys and carries no
timestamp or absolute path. Renderers sort or preserve declaration order, never
dict-iteration order from an unordered source. tests/test_manifest.py asserts
byte-identical output across two independent runs.
