# Roadmap

This page exists to keep the repository honest. It lists what v1.0.0 does not
do, why, and what it would take to get there. Everything in v1.0.0 is shipped
and tested; nothing here is half-implemented behind a flag.

## Shipped in v1.0.0

| Area | Status |
| --- | --- |
| stdio transport (newline-delimited JSON-RPC 2.0) | shipped, tested |
| Streamable HTTP transport (POST + JSON body) | shipped, tested |
| Streamable HTTP transport (POST + SSE body) | shipped, tested |
| 25 conformance checks with weighted score | shipped, tested |
| 13 static security rules (MCP201-MCP213) | shipped, tested |
| tool call with inputSchema and outputSchema validation | shipped, tested |
| inspect with Markdown docs, JSON manifest, HTML report | shipped, tested |
| discover for MCP client config files | shipped, tested |
| init demo server generator | shipped, tested |
| JSON Schema validator (documented keyword subset) | shipped, tested |
| Benchmark harness producing docs/BENCHMARKS.md | shipped |

## Cut from v1.0.0

These were considered and deliberately left out. Each is recorded with the
reason so a future contributor does not have to rediscover it.

### 1. Streamable HTTP GET stream

**What it is.** Streamable HTTP allows a server to push server-initiated
messages (progress notifications, sampling requests) on a long-lived GET
connection, separate from the POST response stream.

**Why it was cut.** Following that stream correctly needs a background reader
thread plus cancellation, reconnection and per-stream sequence handling, none of
which any of the shipped checks require: every check in the suite is a
request/response probe. Shipping a half-working GET loop would be worse than
reporting the limitation, because a hung reader thread is exactly the failure
mode this tool exists to catch.

**What v1 does instead.** HttpTransport.receive fails with a specific message
saying the response body was empty and that the server may be streaming on a
separate GET connection. tests/test_transports.py covers the SSE-in-POST-body
path, which is the common case.

### 2. OAuth bearer tokens for remote endpoints

**What it is.** The MCP authorization spec layers OAuth 2.1 on top of HTTP
transport, including protected-resource metadata discovery.

**Why it was cut.** Doing this properly means implementing RFC 9728 discovery,
PKCE, dynamic client registration and token refresh. That is a project of its
own, and a buggy implementation would be a security hazard in a security tool.
The HTTP target therefore audits open endpoints, which is what a developer runs
against localhost:8000/mcp anyway.

**Workaround for now.** Put an authenticating reverse proxy in front of the
server under test, or run the audit against a local instance.

### 3. Legacy HTTP+SSE transport (protocol 2024-11-05)

**What it is.** The pre-Streamable HTTP pairing of a GET SSE stream for server
messages and a POST endpoint for client messages.

**Why it was cut.** The 2024-11-05 protocol revision is understood well enough
to negotiate (the client accepts it, and INIT-03 scores it), but the transport
itself needs the same background-reader machinery as the GET stream above.
Conformance checks against such a server still work if it also accepts
Streamable HTTP.

### 4. Answering server-initiated requests

**What it is.** Servers may send sampling/createMessage or roots/list to the
client.

**Why it was cut.** Replying with a canned sample message would make the client
lie about its capabilities. Instead Peer answers any server-initiated request
with -32601 and records a warning, and the safe-call probe never triggers
sampling because it only calls tools annotated read-only.

**Next step.** A scripted-client mode where the user supplies responses, so
servers that require sampling can be tested without pretending.

## Next

Ordered by value per unit of work.

1. **Baseline diffing.** inspect --manifest already writes a deterministic
   manifest with no timestamps. A --baseline old.json flag on inspect and audit
   would turn it into a regression gate: fail the build when a tool disappears,
   a required argument is added, or a new critical finding appears relative to
   the committed baseline. This is the highest-value next feature and does not
   need the GET stream.
2. **Schema coverage.** Add dependentSchemas, dependentRequired, prefixItems,
   contains, propertyNames and format assertions (date-time, email, uri, uuid,
   ipv4). The validator already reports unknown keywords, so the failure mode of
   missing coverage is visible rather than silent.
3. **External reference resolution.** Currently local JSON-pointer references
   only. Remote ones need a fetch policy and a cache, which is a design decision
   worth making deliberately (offline by default, --allow-remote-refs to opt in).
4. **JUnit XML output.** conformance --format junit would let existing CI
   dashboards show individual check results without glue code. The Scorecard
   object already has everything a JUnit writer needs.
5. **Mutation testing of the rules themselves.** tests/test_audit.py has a
   positive and a negative case per rule, but nothing proves a rule cannot be
   accidentally disabled. A small harness that deletes each rule body and
   asserts a test fails would close that gap.

## Non-goals

Things this project will not do, so nobody waits for them:

- **A GUI or TUI.** The HTML report is the visual artefact; the terminal is the
  interface. MCP Inspector already does interactive exploration better.
- **Running a server from the tool.** mcp-doctor connects, it does not host.
  Use the server's own runner.
- **Runtime dependencies.** The install footprint is the feature.
- **Being a security guarantee.** A clean audit means "none of 13 rules matched
  this surface". It does not mean the server is safe, and the README says so.
