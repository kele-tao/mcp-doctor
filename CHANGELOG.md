# Changelog

All notable changes to mcp-doctor are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-23

### Added

- JSON-RPC 2.0 client with newline-delimited stdio framing and Streamable
  HTTP/SSE transports, both implemented on the standard library.
- 25-check protocol conformance suite with a weighted 0-100 score, letter
  grade, JSON/Markdown output and a --fail-under gate.
- 13 static security rules (MCP201-MCP213) covering shell-injection surface,
  credential leaks, path traversal, filesystem-root resources, unbounded
  execution strings, prompt-injection phrasing, capability mismatch,
  over-broad tools, missing annotations and tool-name hygiene.
- test-tool command that validates arguments against inputSchema, performs a
  real tools/call, and validates structuredContent against outputSchema.
- inspect with Markdown reference documentation, deterministic manifest
  export and a self-contained HTML report.
- discover for scanning MCP client configuration files for broken and dead
  entries.
- init for generating a runnable demo stdio server.
- Zero-dependency JSON Schema validator covering the keyword subset MCP
  servers actually use, with schema-document sanity checks.
- Benchmark harness (benchmarks/run.py) that produces docs/BENCHMARKS.md and
  benchmarks/results.json.
- Demo server and deliberately insecure server under examples/, both runnable
  offline and used by the test suite.

### Known limitations

- The Streamable HTTP transport does not follow a separate GET stream; only
  the POST response body is consumed.
- No OAuth support for protected remote endpoints.
- The JSON Schema validator implements a documented subset of draft 2020-12.
