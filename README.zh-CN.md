# mcp-doctor

[English](README.md) | **简体中文**

**把它指向任意一个 Model Context Protocol 服务器，一条命令就能拿到一致性评分卡、安全审计，以及一次经过校验的工具调用。**

[![ci](https://github.com/kele-tao/mcp-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/kele-tao/mcp-doctor/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![license](https://img.shields.io/badge/license-MIT-green)
![tests](https://img.shields.io/badge/tests-176%20passing-brightgreen)
![deps](https://img.shields.io/badge/runtime%20deps-0-blue)

![mcp-doctor 对内置 demo 服务器执行 inspect](docs/screenshot.png)

---

## 问题

MCP 服务器如今是工具接入 LLM 客户端的方式，而几乎没有人认真测过它们。
一台服务器如果忘了发送 `notifications/initialized` 通知，在本该返回 `-32601` 的地方返回了
`-32000`，或者给出的 `inputSchema` 连 JSON Schema 校验器都拒绝，它仍然能在某一个客户端里勉强工作，
然后在其他所有地方静默失败。配置层面更糟：`claude_desktop_config.json` 里的一个拼写错误，
最终只表现为“工具不存在”，连一条错误信息都没有。

安全侧的问题更尖锐。一个把 `command` 参数插值进 `shell=True` 的工具，本身就是远程代码执行的原语，
LLM 会很乐意替你扣下扳机，而它在代码里看起来只是三行普普通通的 Python。

`mcp-doctor` 就是缺失的那一步 CI：一条零依赖的命令，对真实服务器说真实的协议，
把发现的问题连同证据和修复建议一起报出来，并在服务器还不够格发布时以非零退出码结束。

---

## 它能做什么

- **真的在说协议。** 在 stdio 上使用换行分隔的 JSON-RPC 2.0（由它自己拉起服务器进程），
  在 Streamable HTTP/SSE 上使用标准库 `urllib`。不依赖任何厂商 SDK，也不用 aiohttp。
- **25 项一致性检查**，覆盖握手、版本协商、能力声明结构、`ping`、错误码（`-32700`、`-32601`、
  `-32602`）、取消、工具 / 资源 / 提示词列表结构、JSON Schema 合法性、分页与延迟，
  并给出加权后的 0-100 分。
- **13 条静态安全规则**（MCP201-MCP213）：shell 注入面、描述符中的凭证泄漏、资源 URI 中的路径穿越、
  以文件系统根目录为资源的暴露、无长度限制的执行字符串、提示词注入措辞、能力声明撒谎、
  过宽的工具，以及缺失的破坏性标注。每一条发现都带证据和具体的修复建议。
- **一次真实的工具调用，三重校验。** `test-tool` 在发送前先用工具自己的 `inputSchema` 校验参数，
  报告 JSON-RPC 的结果，再用声明的 `outputSchema` 校验 `structuredContent`。
- **不用自己写的文档。** `inspect --docs` 会为该服务器输出一份 Markdown 参考文档，
  `--manifest` 则写出确定性的 JSON 清单，CI 任务可以直接 diff。
- **配置发现。** 扫描 `.mcp.json`、Cursor / VS Code / Claude 的配置文件以及 Claude Desktop 的
  profile，找出坏掉的条目：缺失的可执行文件、格式错误的 `args`、`ftp://` URL，
  以及同时声明了 command 和 URL 的条目。
- **自包含的 HTML 报告**，CSS 内联、不依赖 CDN，另外还有 `--format json` 和 `--format md`
  可用于 CI 评论。下面就是上面那台坏掉的服务器生成的报告——评分卡、每一项一致性检查、
  每一条带证据和修复建议的发现，以及推导出这些结论的协议报文记录：

![为那台坏掉的服务器生成的 HTML 报告](docs/report.png)

---

## 安装

目前还没有发布到 PyPI。从代码仓库安装：

```bash
git clone https://github.com/kele-tao/mcp-doctor
cd mcp-doctor
pip install -e .
```

然后：

```bash
mcp-doctor --help          # or: python -m mcp_doctor --help
```

需要 Python 3.10+。运行时依赖：**没有**。`pytest`、`ruff` 和 `coverage` 都放在 `dev`
extra 里（`pip install -e ".[dev]"`）。

---

## 用法

### 1. 检查一台服务器并拿到评分卡

仓库自带一个可以直接运行的 demo 服务器，所以下面这条命令不需要任何额外准备：

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

这两个 `SKIP` 是诚实的：demo 服务器没有声明任何提示词，也没有可以拿来探测 `tools/call`
的零参数只读工具。`inspect --verbose` 会逐项打印每个检查的证据，而不是只给一个分组汇总。

### 2. 审计那个故意写坏的服务器

`examples/buggy_server/` 是一台把教科书里的错误全犯了一遍的小服务器：
它把 `command` 参数插值进 `shell=True`，在工具描述里硬塞了一个 API key，
把 `file:///` 当作资源对外提供，而且从不声明自己实际提供哪些能力。

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

完整那份 5 KB 的报告（每一条发现、每一条修复建议）已提交在
[`docs/samples/audit-buggy.txt`](docs/samples/audit-buggy.txt)，下面是同一条命令的终端截图：

![mcp-doctor 对故意写坏的服务器执行 audit](docs/audit.png)

### 3. 调用一个工具并校验返回结果

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

参数不对就根本不会发到服务器：

```console
$ python -m mcp_doctor test-tool --stdio "python examples/demo_server/server.py" --tool add --args '{"a": "x", "b": 3}'
test-tool add {"a": "x", "b": 3}
  fail  arguments    $.a: expected integer, got string

FAIL  test-tool add
```

### 4. 在 CI 里使用

```yaml
- name: MCP server contract check
  run: |
    pip install -e ./mcp-doctor
    python -m mcp_doctor conformance --stdio "python server.py" --fail-under 90
    python -m mcp_doctor audit       --stdio "python server.py" --fail-on high
    python -m mcp_doctor inspect     --stdio "python server.py" --manifest manifest.json
```

其他命令：

| 命令 | 它回答什么问题 |
| --- | --- |
| `inspect` | 这台服务器暴露了什么，协议面是否健全？ |
| `conformance` | 只看那 25 项检查的评分卡，支持 `--format json/md` 和 `--fail-under` |
| `audit` | 只看安全规则，支持 `--fail-on critical/high/medium/low/info` |
| `test-tool` | 一次真实调用能不能成功，返回内容是否符合契约？ |
| `discover` | 我配置文件里的那些 MCP 服务器真的能启动吗？ |
| `report` | 把自包含的 HTML 报告写到文件里 |
| `init` | 写一份全新的 demo stdio 服务器，马上就能拿来 inspect |

退出码也是契约的一部分：`0` 表示全部通过，`1` 表示有检查失败或有发现达到了 `--fail-on` 阈值，
`2` 表示命令本身用错了。

---

## 工作原理

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

**传输层。** 两种传输实现同一个三方法接口（`send_raw`、`receive`、`close`）。stdio 传输
用 `shlex` 拆分参数来拉起服务器进程，全程不经过 shell，并启动一个读线程，
这样即使服务器很慢也不会阻塞调用方；它还保留最后 400 行 stderr，因此崩溃时能带着服务器
自己的错误信息上报，而不是一句“没有响应”。HTTP 传输逐条 POST JSON-RPC 消息，
同时接受 `application/json` 和 `text/event-stream` 两种响应，并在后续请求中回显
`Mcp-Session-Id` 头。

**Peer。** `Peer` 掌管请求 id，把响应匹配回对应的请求，并处理实际环境中真正会发生的三种情况：
请求进行中收到通知（先暂存，不算致命）、服务器主动发来请求（用 `-32601` 应答，而不是一直挂着）、
以及早先探测留下的过期响应（记为警告并跳过）。分页会沿着 `nextCursor` 最多翻 100 页，
并且能识别出重复的游标，而不是无限循环下去。

**检查与规则。** 一致性检查和安全规则都是针对同一个 `Manifest` 的独立函数，
`Manifest` 是 `initialize`、`tools/list`、`resources/list` 和 `prompts/list`
返回内容的类型化视图。抛异常的检查会被记为 FAIL 并附上异常文本，
这样一个区域出问题永远不会掩盖评分卡的其他部分。依赖能力声明的检查会报告 `SKIP`，
而不是假装通过。

**确定性。** manifest JSON 里没有时间戳，也没有绝对路径，每个渲染器都会对自己的输入排序，
所以对同一台服务器跑两次，输出逐字节相同。测试套件直接断言了这一性质。

---

## 设计取舍

**1. 自己实现协议，而不是导入 SDK。** 官方 MCP Python SDK 非常出色，但一个职责是找协议 bug 的工具，
不能共享它的假设。用约 600 行标准库代码把分帧、信封校验和状态机写出来，同时也意味着
`pip install mcp-doctor` 不会拉取任何东西、可以离线工作，并且能跟一台“坏掉的”服务器对话——
而 SDK 客户端会拒绝连接那种服务器。

**2. 零运行时依赖，手写 JSON Schema。** 另一条路是 `jsonschema`，它的体积是本工具的许多倍，
还会连带引入 `referencing` 和 `rpds-py`。MCP 服务器实际写进 `inputSchema` 的东西，
只是 draft 2020-12 里很窄的一个子集，所以 `mcp_doctor.jsonschema` 只实现这个子集，
同时校验 schema 文档本身（未知关键字、错误的正则、重复的 `required` 名称），
并按 JSON 路径报告错误。取舍是明确写出来的：模块 docstring 和 README 都列出了支持哪些关键字，
遇到不支持的写法会报告出来，而不是悄悄放过。

**3. 分数只有能降下来才有意义。** 两个分数都是针对发现项和检查项的纯扣分算术：
安全分是 `100 - sum(severity penalties)`（critical 40、high 22、medium 10、low 4），
一致性分是加权后的通过比例。没有调参，也没有隐藏系数，
`--fail-on` / `--fail-under` 直接把这个数字变成 CI 门禁。代价是十一条 medium
和十一条 critical 都会被读成“不太行”，这一点靠严重程度那一列一眼就能分辨。

---

## 性能

下面的数字由 `python benchmarks/run.py` 生成并提交在
[`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)。测量环境是 Windows 11、Python 3.12.10、AMD64，
取多次运行的中位数：

| 测量项 | 工作负载 | 中位数 |
| --- | --- | ---: |
| JSON Schema 校验 | 5 MB 负载，26,750 行 | 776 ms |
| Schema 文档检查 | 13 个属性的 inputSchema | 0.02 ms |
| Markdown 参考文档 | 2,000 个工具 | 136 ms |
| 安全审计 | 2,000 个工具 | 89 ms |
| 终端评分卡渲染 | demo 服务器，25 项检查 | 0.17 ms |
| HTML 报告渲染 | demo 服务器，自包含 | 0.18 ms |
| 一致性套件端到端 | 启动 + 握手 + 25 项检查 | 1.06 s |
| `inspect --html` 端到端 | 启动 + inspect + conformance + audit + HTML | 1.22 s |

测试覆盖率是测出来的，不是断言出来的：`python benchmarks/coverage.py` 在 coverage.py 下
跑测试套件，并写出 [docs/COVERAGE.md](docs/COVERAGE.md)。当前 `src/` 的语句覆盖率是
**87.6%**，其中安全规则 97%，传输层 80%。如果 README 和那个文件不一致，
`tests/test_docs.py` 就会失败。

那次 5 MB 的校验跑出的是 6.5 MB/s 的 schema 校验吞吐，性能契约就是照着这个数字写的。
端到端数字主要被进程启动和 25 次往返吃掉，而不是 mcp-doctor 自己的开销：
渲染整份 HTML 报告用不到一毫秒。

---

## 对比

| | mcp-doctor | MCP Inspector | 官方 SDK 客户端 | 手工 curl / echo |
| --- | --- | --- | --- | --- |
| 在 CI 里做协议一致性评分 | 有，`--fail-under` | 仅限交互式 | 不是它的职责 | 没有 |
| 带证据和修复建议的静态安全审计 | 13 条规则 | 没有 | 没有 | 没有 |
| 在真实调用中校验 `outputSchema` | 有 | 没有 | 要自己写代码 | 没有 |
| 为死掉的服务器做配置文件发现 | 有 | 没有 | 没有 | 没有 |
| 依赖体积 | 无 | Node + 浏览器 | SDK + 传输层依赖 | 不适用 |
| 用于探索服务器的交互式界面 | 没有（终端 + HTML） | **有，而且更好** | 没有 | 没有 |
| 完整 JSON Schema 覆盖 | **没有，是文档化的子集** | 通过 Ajv | 通过你自己的校验器 | 不适用 |

想手动戳一台服务器，MCP Inspector 是更好的工具，它覆盖了本工具故意不支持的 schema 词汇表。
要放进流水线的，是 mcp-doctor。

---

## 路线图

1. **Streamable HTTP 的 GET 流。** `HttpTransport` 目前只消费 POST 响应体里回来的内容；
   如果一台服务器把进度放在单独的 GET 连接上流式推送，现在会被报成“没有响应体”，而不是被跟随。
2. **OAuth bearer token 支持**，用于远程 MCP 端点，这样 `--http` 能审计受保护的服务器，
   而不只是完全开放的服务器。
3. **用脚本化客户端做动态探测。** 应答 `sampling/createMessage` 和 `roots/list`，
   让那些依赖它们的服务器也能端到端测试。
4. **超出文档化子集的 schema 覆盖**：`dependentSchemas`、`prefixItems`、`format` 断言，
   以及外部 `$ref` 解析。
5. **基线对比。** `mcp-doctor inspect --manifest` 已经能写出确定性的 manifest，
   再加一个 `--baseline old.json` 模式，就能把它变成回归门禁。

从 v1.0 里砍掉、记录在这里而不是做一半的：GET 流、OAuth，
以及早于 Streamable HTTP 的旧版纯 SSE 传输。

---

## 参与贡献

带一台能复现问题的最小服务器的 bug 报告，是最有用的东西。开发流程见
[CONTRIBUTING.md](CONTRIBUTING.md)，报告 mcp-doctor 自身漏洞见
[SECURITY.md](SECURITY.md)。

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -q
python benchmarks/run.py
```

## 许可证

MIT。见 [LICENSE](LICENSE)。
