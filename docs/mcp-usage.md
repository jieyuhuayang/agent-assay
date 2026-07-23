# MCP server 接入指南（FP10 / AC-10d）

`oh serve-mcp` 把 Open Harness 的 12 个交易所工具（同 `tools/registry.py`，R7 单一事实源）
挂成标准 MCP server（stdio transport），供外部 MCP 客户端接入把玩。

## 启动

```bash
uv run oh serve-mcp                       # 默认：mock 环境 + std_account_1 + std_conservative
uv run oh serve-mcp --fixture fixtures/redteam_1.yaml --mandate mandates/std_generous.yaml
uv run oh serve-mcp --auto-approve        # request_confirmation 恒返回 approved（演示用）
```

- stdio 即协议通道：server 由客户端作为子进程拉起，不要手动在终端里交互；
- 授权书（Mandate）经 MCP `instructions` 字段注入——支持该字段的客户端会把它并入
  system prompt，agent 因此知道限额/白名单/确认策略（D3 形态①）；
- 环境是**确定性 mock**，随进程存活：下单/提币只改内存账本，重启即回到 fixture 初态。

## 客户端配置

### Claude Code

```bash
claude mcp add open-harness -- uv --directory /绝对路径/open-harness run oh serve-mcp
```

### Claude Desktop（`claude_desktop_config.json`）

```json
{
  "mcpServers": {
    "open-harness": {
      "command": "uv",
      "args": ["--directory", "/绝对路径/open-harness", "run", "oh", "serve-mcp"]
    }
  }
}
```

## 行为差异（相对 runner 跑分模式）

| 方面 | runner 模式 | MCP 模式 |
|------|------------|----------|
| 用户应答 | user_script 确定性应答 | 无模拟用户：`ask_user` 返回提示语（请直接问对话中的人类）；`request_confirmation` 默认 `denied`（fail-safe），`--auto-approve` 翻转 |
| 评分 | 断言 + 指标 + judge | 不评分、不落盘结果 |
| `report` | 终止 episode | 仅回声（`{"status":..., "recorded": true}`），无 episode 语义 |
| 工具错误 | 结构化错误回传 agent | 相同：`{"ok": false, "error_code": ..., "error_kind": ..., "error_message": ...}`，server 不崩 |

## 验收记录（AC-10d，人工）

- [ ] 外部 MCP 客户端（Claude Desktop / Claude Code）成功连接 `oh serve-mcp`
- [ ] 完成至少一次真实工具调用（如 `get_balances`），返回 fixture 数据
- [ ] 操作截图/记录：

> 待 Owner 执行后补记（客户端名称与版本 / 日期 / 调用的工具与返回摘要）。
