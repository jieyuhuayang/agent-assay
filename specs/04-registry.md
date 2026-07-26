# specs/04 — FP04 工具注册表（M1）

> 对应 specs/00-milestones.md · FP04。守护红线：R7 R8 D3。依赖 FP03。

## 设计定案

- **单一事实源（D2/R7）**：`tools/registry.py` 定义 12 个 `ToolDef{name, description,
  params_model(pydantic), category, irreversible_fn, handler}`。JSON schema 一律由
  `params_model.model_json_schema()` 生成——runner（FP05）与 MCP server（FP10）都从这里取，
  仓库内不允许出现第二份工具 schema（tripwire 测试 `test_r7_no_tool_schema_outside_registry`）。
- **双层校验（第 5 节）**：
  - schema 层 = pydantic 校验（类型/枚举/必填/extra=forbid 拒幻觉参数），失败 →
    `error_kind="schema_error"`，`error_code="SCHEMA_VALIDATION"`（未知工具 `UNKNOWN_TOOL`；
    v0.2 起「未知」按调用方 profile 判定——名字在全集但不在当前 profile 同记
    `UNKNOWN_TOOL`，见 specs/14 §1 G4）；
  - 语义层 = env 抛出的 `ExchangeError` → `error_kind="semantic_error"`，错误码原样透传
    （LOT_SIZE 等），agent 可读错误自我修正；每次记入 `ToolInvocation`（轨迹与指标素材）。
- **不可逆元数据（R8）**：只在 `ToolDef.irreversible_fn` 定义——place_order 按参数动态
  （market→true，limit/stop_limit→false），withdraw 恒 true，其余 false。
  `ToolInvocation.irreversible` 是该元数据的唯一下游快照；FP07 断言只读它。
  语义失败的调用同样打标（区分「尝试」与「执行」由 `ok` 字段负责）。
- **D3 结构保证**：`ToolContext` 只有 env + 交互回调，**没有 mandate 字段**——工具层
  想做 mandate 校验在类型上就不可能（守护测试断言之）。
- **类别**：readonly ×6 / trading ×3（place/cancel/withdraw）/ interactive ×2 / terminal ×1
  （report；runner 据 category 终止 episode）。
- stop_limit 在 place_order 描述中按 D-b 写明「通用条件单、不区分方向」的环境语义；
  market/withdraw 的不可逆性也写进描述（对被测 agent 公平：信息在卡片上，看不看是它的事）。
- 金额参数用 Money：JSON schema 表示为 decimal 字符串（R9），float 输入直接 schema_error。

## AC 与测试（同 specs/00 FP04 条目）

`tests/test_registry.py`：test_twelve_tools_signatures / test_schema_error_recorded /
test_semantic_error_exchange_codes / test_irreversible_metadata_dynamic /
test_no_mandate_enforcement_in_tool_layer；`tests/test_redlines.py::test_r7_no_tool_schema_outside_registry`。
