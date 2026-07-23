# specs/10 · FP10 MCP server（M3）

> 上游：specs/00 §FP10（AC-10a–10d）、KICKOFF §5 D2（工具定义单一事实源，双通道暴露）、
> §12（技术栈：官方 `mcp` Python SDK）、红线 R7。
> 本规格是实现契约；与 specs/00 冲突时以 specs/00 为准。

## 1. 范围与目标

把 `tools/registry.py` 里的同一份工具集，以标准 MCP server（stdio transport）形式暴露给
外部 MCP 客户端（Claude Desktop / Claude Code 等）。这是 D2 的第二消费通道：
**registry 是唯一 schema 来源，MCP 层只做反射，不得出现第二份工具定义**（R7）。

交付物：

- `src/agent_assay/mcp_server.py` — 反射层 + stdio server；
- `assay serve-mcp` 子命令（typer）；
- `docs/mcp-usage.md` — 外部客户端接入说明（AC-10d 人工项的操作底稿）；
- 守护测试 `test_r7_mcp_schemas_match_registry` + `tests/test_mcp_server.py`。

## 2. 关键决策

### D-e · SDK 形态：低层 `mcp.server.lowlevel.Server`，不用 FastMCP

FastMCP 从 Python 函数签名生成 schema —— 那会成为 registry 之外的第二份 schema 来源，
结构上违反 R7。因此用低层 Server API：

- `list_tools` handler 返回 `types.Tool(name=t.name, description=t.description,
  inputSchema=t.json_schema())`，逐字段来自 `ToolDef`；
- `call_tool` handler 直接调 `registry.execute_tool(name, arguments, ctx)`。

### D-f · MCP 模式没有模拟用户（交互工具策略）

runner 模式下 `ask_user` / `request_confirmation` 由 user_script 应答；MCP 模式下
「用户」是 MCP 客户端对话里的人类操作者，harness 无法代答。策略（fail-safe）：

- `ask_user` → 固定回复字符串：
  `"[mcp-mode] There is no simulated user in this session. Ask the human operator directly in the chat conversation."`
- `request_confirmation` → 默认返回 `"denied"`（不可逆操作默认不予放行；人类要放行
  应在对话中明示，agent 据 D3 仍可直接执行工具——工具层本就不硬拦）；
  `--auto-approve` 开关翻转为恒 `"approved"`（把玩演示用，仅影响 MCP 模式）。

该策略只存在于 serve-mcp 的 ToolContext 回调里，不触碰 registry（R7/D3 不受扰动）。

### D-g · Mandate 经 server `instructions` 注入

`--mandate` 在 MCP 模式的落点：用 FP05 的 `assemble_system_prompt(mandate)` 渲染后放进
MCP server 的 `instructions` 字段（initialize 响应可见，客户端通常把它并入 system prompt）。
这是 D3 形态①（prompt 注入、agent 可读）在 MCP 通道的对应物。评分侧（形态②）在 MCP
模式不存在——serve-mcp 只是环境暴露，不产生结果 JSON、不评分。

## 3. `mcp_server.py` 契约

```
build_mcp_tools() -> list[mcp.types.Tool]        # 纯反射，无 IO；AC-10a 的被测对象
serve(env, mandate, *, auto_approve=False) -> None  # 阻塞运行 stdio server（内部 asyncio.run）
```

- `build_mcp_tools()`：对 `registry.all_tools()` 逐个映射
  `name → Tool.name`、`description → Tool.description`、`json_schema() → Tool.inputSchema`，
  顺序与 registry 一致，全量 12 个工具（含交互工具与 report——工具集本身不裁剪）。
- `call_tool(name, arguments)`：调 `execute_tool`；**永不 raise**（沿用 registry 契约），
  返回单个 `TextContent`，text 为 JSON（`ensure_ascii=False`）：
  `{"ok": bool, "result": Any, "error_code": str|null, "error_kind": str|null,
  "error_message": str|null}`（即 `ToolInvocation` 去掉 tool/arguments/irreversible——
  前两者客户端自知，后者是评分侧元数据不外泄语义）。
  `InvariantViolation` 例外：环境账本损坏必须炸出（与 registry 同款护栏）。
- server 元数据：name=`agent-assay`，instructions=渲染后的 mandate prompt。
- `report` 工具在 MCP 模式无 episode 语义，行为不变（返回 `{"status":..., "recorded": true}`），
  文档里注明它在 MCP 模式只是回声。

## 4. `assay serve-mcp` CLI

```
assay serve-mcp [--env mock] [--fixture fixtures/std_account_1.yaml]
             [--mandate mandates/std_conservative.yaml] [--root .] [--auto-approve]
```

- `--env mock|testnet`；v0.1 先支持 mock，`testnet` 在 FP11 落地前报错退出（exit 2，
  提示随 FP11 到来），FP11 落地后接同一 env 工厂；
- fixture / mandate 相对 `--root` 解析；文件不存在 → 明确报错 exit 2（stderr），不得静默；
- stdio 是 MCP 传输通道：**协议流量走 stdout，人类可读日志一律走 stderr**；
- 进程常驻直至 stdin EOF（客户端断开）。
- `python -m agent_assay.cli` 需可直接执行（测试用 stdio 客户端以此拉起子进程）。

## 5. 测试映射

| AC | 测试 | 要点 |
|----|------|------|
| AC-10a | `test_redlines.py::test_r7_mcp_schemas_match_registry` | `build_mcp_tools()` 与 `all_tools()` 数量、顺序、name/description/inputSchema 逐字段相等（schema 用 `==` 深比较） |
| AC-10b | `test_mcp_server.py::test_stdio_tool_call_roundtrip` | mcp SDK stdio 客户端拉起 `python -m agent_assay.cli serve-mcp` 子进程：initialize → list_tools（12 个）→ call_tool `get_balances` → 结果含 fixture 余额 |
| AC-10c | `test_mcp_server.py::test_serve_mcp_flags` | 换 `--fixture fixtures/redteam_1.yaml` + `--mandate mandates/std_generous.yaml`：余额来自 redteam_1；initialize.instructions 含 generous 限额「10000」 |
| 补充 | `test_mcp_server.py::test_mcp_interactive_tools_policy` | 默认 `request_confirmation` → denied、`ask_user` → mcp-mode 提示；`--auto-approve` → approved |
| 补充 | `test_mcp_server.py::test_mcp_tool_error_is_structured` | 非法参数 call_tool → `ok=false, error_kind=schema_error`，进程不死、可继续调用 |
| AC-10d | 人工（Owner）：外部客户端接入 + 一次真实调用，记录入 `docs/mcp-usage.md` | 底稿见该文档 |

测试为同步函数内 `asyncio.run(...)`（不引入 pytest-asyncio）；子进程 `cwd` / `--root`
指向仓库根。mcp SDK 进主依赖（`mcp>=1.2,<2`）。

## 6. 红线接触面

- R7：反射 + AC-10a 守护；仓库内不得新增任何工具 schema 字面量（`test_r7_no_tool_schema_outside_registry` 继续生效）。
- R1/R2：MCP 模式不引入新外联（mock env 零网络）；testnet 接入后沿用 FP11 的网络层与 key 纪律。
- D3：serve-mcp 不做任何 mandate 硬拦截；mandate 只经 instructions 注入。
