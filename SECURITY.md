# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | yes |

## Reporting a vulnerability

Please do not open a public issue for a vulnerability in mcp-doctor itself.

Report it privately through GitHub's "Report a vulnerability" button on the
Security tab of the repository, or email the maintainer listed in the repository
profile. Include:

- the version of mcp-doctor,
- the command you ran and the server you pointed it at,
- what happened, and what you expected.

You should get an acknowledgement within a few days. Fixes are released as a
patch version and credited in CHANGELOG.md unless you prefer to stay anonymous.

## What counts as a vulnerability in this tool

mcp-doctor spawns a child process and sends JSON to it. Things worth reporting:

- Command injection through a --stdio value, an argument, or a config file.
- Reading files outside the paths the user asked for.
- A crafted server response that makes mcp-doctor write outside a requested
  output path, or execute code.
- A denial of service where a hostile server response makes mcp-doctor hang
  indefinitely rather than timing out.

## What does not

- mcp-doctor connects to whatever server you point it at. If that server is
  malicious, it can return whatever data it likes; use --http only against
  endpoints you trust.
- Findings reported by the audit describe the server under test, not
  mcp-doctor. A clean audit is not a security guarantee.
- The demo servers in examples/ are intentionally insecure teaching material.
  examples/buggy_server/server.py executes shell commands on purpose. Never run
  it outside a sandbox.
