# Contributing to mcp-doctor

Thanks for taking the time to help. This document is short on purpose.

## Development loop

    git clone https://github.com/kele-tao/mcp-doctor
    cd mcp-doctor
    python -m venv .venv
    . .venv/bin/activate          # Windows: .venv\Scripts\activate
    pip install -e ".[dev]"

Then, before every commit:

    ruff check .
    ruff format --check .
    pytest -q

CI runs the same three commands on Python 3.10, 3.11 and 3.12, plus a CLI smoke
test (python -m mcp_doctor --help). No network access is needed: every test
talks to a server that is spawned from this repository.

## What makes a good bug report

The single most useful thing you can attach is a minimal MCP server that
reproduces the problem, plus the exact command you ran and the output you got.
If the server is proprietary, describe the JSON-RPC frames instead.

    python -m mcp_doctor inspect --stdio "python my_server.py" --verbose

--verbose prints the evidence for every check, which is usually enough to
localise the issue.

## Adding a conformance check

1. Write a function in src/mcp_doctor/conformance.py that takes the _Probe and
   returns a Check.
2. Give it a stable id that starts with its area (INIT-, PROTO-, TOOLS-, RES-,
   PROMPT-, CAP-, META-, CALL-, PERF-) so the terminal summary groups it.
3. Register it in CHECKS.
4. Add a test in tests/test_conformance.py that asserts the outcome against the
   bundled demo server or a purpose-built fixture.

## Adding a security rule

1. Write a _rule function in src/mcp_doctor/audit.py returning Finding objects
   with a stable MCP2xx id, a severity, evidence and a fix.
2. Register it in RULES and in audit().
3. Add the rule to the README table if it is user-visible.
4. Add a positive test and a negative test in tests/test_audit.py. A rule that
   fires on a clean server is a bug.

## Code style

- Python 3.10+, line length 100, ruff defaults from pyproject.toml.
- Type hints on every public function; "from __future__ import annotations" at
  the top of each module.
- No new runtime dependencies. This is a hard rule: the tool's value is that it
  installs with nothing.
- User-facing errors go through UsageError (exit 2) or TransportError (exit 1)
  so the CLI never prints a traceback.

## Commit messages

Short imperative subject line, blank line, then a paragraph explaining the
tradeoff if there is one. Reference the issue number when there is one.
