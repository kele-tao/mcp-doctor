# mcp-doctor

**Point it at any Model Context Protocol server and get a conformance scorecard, a security audit and a validated tool call in one command.**

[![ci](https://github.com/kele-tao/mcp-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/kele-tao/mcp-doctor/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![license](https://img.shields.io/badge/license-MIT-green)
![tests](https://img.shields.io/badge/tests-176%20passing-brightgreen)
![deps](https://img.shields.io/badge/runtime%20deps-0-blue)

![mcp-doctor inspect on the bundled demo server](docs/screenshot.png)

---

## The problem

MCP servers are now the way tools reach LLM clients, and almost nobody tests them.
A server that forgets to send the `notifications/initialized` notification, returns
`-32000` where the spec demands `-32601`, or ships an `inputSchema` that a JSON Schema
validator rejects will still work in exactly one client and fail silently everywhere
else. Configuration is worse: a typo in `claude_desktop_config.json` surfaces as
"the tool does not exist" with no error message at all.

The security side is sharper. A tool that interpolates its `command` argument into
`shell=True` is a remote code execution primitive that an LLM will happily pull the
trigger on, and it looks like three lines of ordinary Python.

`mcp-doctor` is the missing CI step: one dependency-free command that speaks the
protocol to a real server, reports what it finds with evidence and a fix, and exits
non-zero when the server is not good enough to ship.

---

## What it does

- **Speaks the protocol for real.** Newline-delimited JSON-RPC 2.0 over stdio (spawns
  the server) and Streamable HTTP/SSE over stdlib `urllib`. No vendor SDK, no aiohttp.
- **25 conformance checks** across handshake, version negotiation, capability shape,
  `ping`, error codes (`-32700`, `-32601`, `-32602`), cancellation, tool/resource/prompt
  list shape, JSON Schema validity, pagination and latency, with a weighted 0-100 score.
- **13 static security rules** (MCP201-MCP213): shell-injection surface, credential leaks
  in descriptors, path traversal in resource URIs, filesystem-root resources, unbounded
  execution strings, prompt-injection phrasing, capability lying, over-broad tools and
  missing destructive annotations. Every finding carries evidence and a concrete fix.
- **A real tool call, validated three ways.** `test-tool` checks the arguments against
  the tool's own `inputSchema` before sending, reports the JSON-RPC outcome, then
  validates `structuredContent` against the declared `outputSchema`.
- **Documentation you do not have to write.** `inspect --docs` emits a Markdown reference
  for the server, and `--manifest` writes a deterministic JSON manifest a CI job can diff.
- **Config discovery.** Scans `.mcp.json`, Cursor/VS Code/Claude configs and the Claude
  Desktop profile for broken entries: missing binaries, malformed `args`, `ftp://` URLs,
  entries that declare both a command and a URL.
- **Self-contained HTML report** with inline CSS and no CDN, plus `--format json` and
  `--format md` for CI comments. Here it is for the broken server above - scorecards,
  every conformance check, every finding with evidence and fix, and the protocol
  transcript it was derived from:

![the generated HTML report for the broken server](docs/report.png)

---

## Install

Not on PyPI yet. From a checkout:

```bash
git clone https://github.com/kele-tao/mcp-doctor
cd mcp-doctor
pip install -e .
```

Then:

```bash
mcp-doctor --help          # or: python -m mcp_doctor --help
```

Requires Python 3.10+. Runtime dependencies: **none**. `pytest`, `ruff` and `coverage`
live in the `dev` extra (`pip install -e ".[dev]"`).

---

## Usage

### 1. Inspect a server and get a scorecard

The repository ships a runnable demo server, so this works with no other setup:

```console
$ python -m mcp_doctor inspect --stdio "python examples/demo_server/server.py"
```

```text
demo-tools 1.0.0   protocol 2025-06-18
3 tools   1 resources   0 templates   0 prompts
capabilities: resources, tools
instructions: Small demo server for mcp-doctor. Try the add tool.

TOOLS
NAME        DESCRIPTION                                     ARGS  REQUIRED  OUTPUT
----------  ----------------------------------------------  ----  --------  ------
add         Add two integers and return the sum.            2     a,b       yes
echo        Return the given text unchanged. Useful for...  2     text      no
word_count  Count words and lines in a block of text.       1     text      no

RESOURCES
URI              NAME           MIME
---------------  -------------  -------------
memo://handbook  Team handbook  text/markdown

demo-tools 1.0.0  |  protocol 2025-06-18
conformance    ############################ 100/100   grade A
23 pass  0 warn  0 fail  2 skip

AREA    RESULT
------  ---------
INIT    4 pass
PROTO   8 pass
TOOLS   5 pass
RES     1 pass
PROMPT  -  1 skip
CAP     2 pass
META    2 pass
CALL    -  1 skip
PERF    1 pass

security audit
security       ############################ 100/100   grade A
0 critical  0 high  0 medium  0 low  0 info   (13 static rules evaluated)

  no findings: nothing in the advertised surface matches a rule
```

The two `SKIP`s are honest: the demo server advertises no prompts, and it has no
zero-argument read-only tool to probe `tools/call` with. `inspect --verbose` prints
the evidence for every check instead of the grouped summary.

### 2. Audit the deliberately broken server

`examples/buggy_server/` is a small server that makes every mistake in the book:
it interpolates a `command` argument into `shell=True`, embeds an API key in a tool
description, serves `file:///` as a resource and never declares the capabilities it
serves.

```console
$ python -m mcp_doctor audit --stdio "python examples/buggy_server/server.py"; echo "exit=$?"
```

```text
security audit
security       ............................   0/100   grade F
2 critical  9 high  4 medium  1 low  0 info   (13 static rules evaluated)

RULE    SEVERITY  FINDING                                                       WHERE
------  --------  ------------------------------------------------------------  ----------------------------
MCP201  CRITICAL  tool 'run_shell_command' mentions shell execution and tak...  tool:run_shell_command
MCP202  CRITICAL  tool 'fetch_url' leaks a credential-shaped string             tool:fetch_url
MCP203  HIGH      resource 'Any file by path' exposes a file/system URI tem...  resource:Any file by path
MCP204  HIGH      resource 'Entire filesystem' advertises a filesystem root     resource:Entire filesystem
MCP205  HIGH      tool 'run_shell_command' accepts unbounded strings for ex...  tool:run_shell_command
MCP207  HIGH      server lists tools without advertising the tools capabili...  capabilities
MCP208  MEDIUM    tool 'read_delete_update_create_file' bundles many destru...  tool:read_delete_update_c...
MCP209  MEDIUM    tool 'read_delete_update_create_file' is destructive but ...  tool:read_delete_update_c...
MCP210  LOW       no tool declares an outputSchema                              tools

  MCP201 tool 'run_shell_command' mentions shell execution and takes free-form string input
    evidence: description/name matches shell command; string arguments: command, cwd
    fix:      pass argv arrays to subprocess without shell=True, validate the argument
              against an allowlist, and reject shell metacharacters
exit=1
```

The full 5 KB report (every finding, every fix) is committed at
[`docs/samples/audit-buggy.txt`](docs/samples/audit-buggy.txt), and here is the same
command rendered as a terminal capture:

![mcp-doctor audit on the deliberately broken server](docs/audit.png)

### 3. Call one tool and validate the answer

```console
$ python -m mcp_doctor test-tool --stdio "python examples/demo_server/server.py" --tool add --args '{"a": 19, "b": 23}'
```

```text
test-tool add {"a": 19, "b": 23}
  ok    arguments    arguments satisfy inputSchema
  ok    call         server accepted the request
  ok    result       1 content item(s), all well formed
  ok    outputSchema structuredContent matches outputSchema
    {
      "content": [
        {
          "text": "42",
          "type": "text"
        }
      ],
      "structuredContent": {
        "sum": 42
      }
    }

PASS  test-tool add
```

Bad arguments never reach the server:

```console
$ python -m mcp_doctor test-tool --stdio "python examples/demo_server/server.py" --tool add --args '{"a": "x", "b": 3}'
test-tool add {"a": "x", "b": 3}
  fail  arguments    $.a: expected integer, got string

FAIL  test-tool add
```

### 4. Use it in CI

```yaml
- name: MCP server contract check
  run: |
    pip install -e ./mcp-doctor
    python -m mcp_doctor conformance --stdio "python server.py" --fail-under 90
    python -m mcp_doctor audit       --stdio "python server.py" --fail-on high
    python -m mcp_doctor inspect     --stdio "python server.py" --manifest manifest.json
```

Other commands:

| Command | What it answers |
| --- | --- |
| `inspect` | What does this server expose, and is the protocol surface sound? |
| `conformance` | Just the 25-check scorecard, with `--format json/md` and `--fail-under` |
| `audit` | Just the security rules, with `--fail-on critical/high/medium/low/info` |
| `test-tool` | Does one real call succeed, and is the response contract-shaped? |
| `discover` | Are the MCP servers in my config files actually startable? |
| `report` | Write the self-contained HTML report to a file |
| `init` | Write a fresh demo stdio server you can inspect immediately |

Exit codes are part of the contract: `0` everything passed, `1` a check failed or a
finding met the `--fail-on` threshold, `2` the command itself was wrong.

---

## How it works

```mermaid
flowchart LR
    A[CLI target<br/>--stdio CMD or --http URL] --> B{Transport}
    B -->|spawn + newline framing| C[StdioTransport]
    B -->|POST / SSE via urllib| D[HttpTransport]
    C --> E[Peer<br/>JSON-RPC correlation]
    D --> E
    E --> F[initialize handshake]
    F --> G[build_manifest<br/>tools / resources / prompts]
    G --> H[25 conformance checks]
    G --> I[13 static security rules]
    G --> J[renderers]
    H --> J
    I --> J
    J --> K[terminal / JSON / Markdown / HTML]
    J --> L[exit code 0 or 1]
```

**Transport layer.** Both transports implement one three-method interface (`send_raw`,
`receive`, `close`). The stdio transport spawns the server with `shlex` argument
splitting, never through a shell, starts a reader thread so a slow server cannot block
the caller, and keeps the last 400 stderr lines so a crash can be reported with the
server's own error message instead of "no response". The HTTP transport POSTs each
JSON-RPC message, accepts both `application/json` and `text/event-stream` responses,
and echoes the `Mcp-Session-Id` header on later requests.

**Peer.** `Peer` owns request ids, matches responses to requests, and handles the three
things that actually happen in the wild: notifications arriving mid-request (stashed,
not fatal), the server sending an unsolicited request (answered with `-32601` rather
than hanging), and stale responses from earlier probes (recorded as a warning and
skipped). Pagination follows `nextCursor` up to 100 pages and detects a repeated cursor
instead of looping forever.

**Checks and rules.** Conformance checks and security rules are independent functions
over the same `Manifest`, which is the typed view of what `initialize`, `tools/list`,
`resources/list` and `prompts/list` returned. A check that raises is recorded as a FAIL
with the exception text, so one broken area never hides the rest of the scorecard.
Capability-gated checks report `SKIP` rather than a false pass.

**Determinism.** The manifest JSON has no timestamps and no absolute paths, and every
renderer sorts its inputs, so two runs against the same server produce byte-identical
output. The test suite asserts that property directly.

---

## Design decisions

**1. Implement the protocol instead of importing the SDK.** The official MCP Python SDK
is excellent, but a tool whose job is to find protocol bugs cannot share its
assumptions. Writing the framing, the envelope validation and the state machine in
~600 lines of stdlib also means `pip install mcp-doctor` pulls nothing, works offline,
and can talk to a *broken* server that an SDK client would refuse to connect to.

**2. Zero runtime dependencies, hand-written JSON Schema.** The alternative was
`jsonschema`, which is many times the size of this tool and drags in `referencing` and
`rpds-py`. What MCP servers actually put in `inputSchema` is a narrow slice of draft
2020-12, so `mcp_doctor.jsonschema` implements exactly that slice, validates the schema
document itself (unknown keywords, bad patterns, duplicate `required` names), and
reports errors by JSON path. The tradeoff is explicit: the module docstring and the
README both list which keywords are supported, and unsupported constructs are reported
rather than silently passed.

**3. A score only means something if it can go down.** Both scores are plain penalty
arithmetic over the findings and checks: security is `100 - sum(severity penalties)`
(critical 40, high 22, medium 10, low 4); conformance is the weighted pass fraction.
There is no tuning and no hidden coefficient, and `--fail-on`/`--fail-under` turn the
number into a CI gate. The tradeoff is that eleven medium findings and eleven critical
findings both read as "not fine", which the severity column fixes at a glance.

---

## Performance

Numbers below are produced by `python benchmarks/run.py` and committed to
[`docs/BENCHMARKS.md`](docs/BENCHMARKS.md). Measured on Windows 11, Python 3.12.10,
AMD64, medians over repeated runs:

| Measurement | Workload | Median |
| --- | --- | ---: |
| JSON Schema validation | 5 MB payload, 26,750 rows | 776 ms |
| Schema document check | 13-property inputSchema | 0.02 ms |
| Markdown reference doc | 2,000 tools | 136 ms |
| Security audit | 2,000 tools | 89 ms |
| Terminal scorecard render | demo server, 25 checks | 0.17 ms |
| HTML report render | demo server, self-contained | 0.18 ms |
| Conformance suite end to end | spawn + handshake + 25 checks | 1.06 s |
| `inspect --html` end to end | spawn + inspect + conformance + audit + HTML | 1.22 s |

Test coverage is measured, not asserted: `python benchmarks/coverage.py` runs the suite
under coverage.py and writes [docs/COVERAGE.md](docs/COVERAGE.md). The current total is
**87.6%** of statements in `src/`, with the security rules at 97% and the transport at 80%.
`tests/test_docs.py` fails if the README and that file disagree.

The 5 MB validation run is 6.5 MB/s of schema-checked JSON, which is the number the
performance contract is written against. The end-to-end numbers are dominated by
process spawn and the 25 round trips, not by mcp-doctor's own work: rendering the
whole HTML report takes under a millisecond.

---

## Comparison

| | mcp-doctor | MCP Inspector | Official SDK client | Manual curl / echo |
| --- | --- | --- | --- | --- |
| Protocol conformance scoring in CI | yes, `--fail-under` | interactive only | not its job | no |
| Static security audit with evidence + fix | 13 rules | no | no | no |
| Validates `outputSchema` on a real call | yes | no | requires you to write code | no |
| Config-file discovery for dead servers | yes | no | no | no |
| Dependency footprint | none | Node + browser | SDK + transport deps | n/a |
| Interactive UI for exploring a server | no (terminal + HTML) | **yes, better** | no | no |
| Full JSON Schema coverage | **no, a documented subset** | via Ajv | via your validator | n/a |

MCP Inspector is the better tool for poking at a server by hand, and it covers schema
vocabulary this tool deliberately does not. mcp-doctor is the one you put in a pipeline.

---

## Roadmap

1. **Streamable HTTP GET stream.** `HttpTransport` currently consumes only what comes
   back on the POST body; a server that streams progress on the separate GET connection
   is reported as "no response body" rather than followed.
2. **OAuth bearer-token support** for remote MCP endpoints, so `--http` can audit a
   protected server rather than only an open one.
3. **Dynamic probes with a scripted client.** Answer `sampling/createMessage` and
   `roots/list` so servers that require them can be tested end to end.
4. **Schema coverage beyond the documented subset**: `dependentSchemas`, `prefixItems`,
   `format` assertions, and external `$ref` resolution.
5. **Baseline diffing.** `mcp-doctor inspect --manifest` already writes a deterministic
   manifest; a `--baseline old.json` mode would turn it into a regression gate.

Cut from v1.0 and recorded here rather than half-built: the GET stream, OAuth, and the
older SSE-only transport that predates Streamable HTTP.

---

## Contributing

Bug reports with a minimal server that reproduces the problem are the most useful thing
you can send. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development loop and
[SECURITY.md](SECURITY.md) for reporting a vulnerability in mcp-doctor itself.

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -q
python benchmarks/run.py
```

## License

MIT. See [LICENSE](LICENSE).
