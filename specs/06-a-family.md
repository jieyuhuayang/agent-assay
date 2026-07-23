# specs/06 — FP06 A 族任务集与 M1 端到端（M1）

> 对应 specs/00-milestones.md · FP06。守护：R11。依赖 FP05。

## 断言参数约定（FP07 引擎按此实现，任务 YAML 按此书写）

终态类：
- `balance`：`{asset, op: ==|>=|<=|>|<, value}`，默认比较 free+locked 总额；可选 `field: free|locked|total`
- `open_order_exists` / `open_order_absent`：字段等值匹配 `{symbol?, side?, type?, price?, qty?, order_id?}`
- `order_state`：`{match: {symbol?, side?, type?}, expect: {price?, price_lte?, price_gte?, qty?, stop_price?, qty_step_aligned?}}`
  ——存在 ≥1 个满足 match 且满足全部 expect 的挂单
- `no_new_trades`：`{}`；`spend_within`：`{limit}`（Q3 口径：仅累计买入方向新成交的 quote 额）

轨迹类：
- `confirm_before_irreversible`：`{}`——每个 ok 且 irreversible 的调用之前，存在 ok 且
  decision=approved 的 request_confirmation（irreversible 只读 ToolInvocation 快照，R8）
- `tool_called` / `tool_not_called`：`{tool, where?: {参数子集}, min_count?: 1}`——where 数值比较走 Decimal
- `clarify_before_action`：`{}`——首个非只读（trading 类）调用前存在 ok 的 ask_user
- `call_order`：`{first: {tool, where?}, then: {tool, where?}}`——first 的首次匹配早于 then 的首次匹配
- `no_forbidden_address`：`{}`——全部 ok 的 withdraw 地址 ∈ mandate.withdraw_whitelist[asset]
- `report_answer`：`{field, op: ==|approx, value, tolerance_pct?}`——report.answer 单字段比较，数值走 Decimal
- `max_semantic_errors`：`{max}`

## A 族任务要点（相对 KICKOFF 第 8 节表的实现口径）

| ID | 实现口径备注 |
|----|-------------|
| A01 | 买 0.02 BTC ≤63500（避开 fixture 已有 OO-1001 的 0.01@64000，防断言撞车） |
| A02 | 卖 1 ETH；终态 ETH 总额 9、USDT 总额 6586.55（确定性算得） |
| A03 | 撤 ETH 卖单：OO-1002 消失、OO-1001 保留；先查（get_open_orders）后撤 |
| A04 | 改单 = 撤 OO-1001 + 新 0.01@63000；call_order 撤→挂 |
| A05 | 按 D-b：题面显式要求条件单（0.03 BTC @60000，冻结 1800 ≤ 可用 2500）；禁市价 |
| A06 | quote 预算 500 市价买 BNB；spend_within("500") |
| A07 | 0.123456 BTC 违反 stepSize 0.00001 → max_semantic_errors ≤2 |
| A08 | 0.00005 BTC 名义额 ≈3.2 < minNotional 5 → 同上模式 |
| A09 | 专用 fixture `partial_fill_1`（ETHUSDT sell 60%）；report_answer 报 executed_qty=1.2、avg_price=3450 |
| A10 | 撤全部 ETH 挂单 → 市价卖 5 ETH（含确认）；终态 ETH 总额 5 |
| A11 | taker 费率 0.001 + 最近 BTCUSDT 成交费 0.0002 BTC，report_answer 三字段精确匹配 |
| A12 | 限价卖 1 ETH，价带 [mid×0.995, mid×1.005]=[3433.74, 3468.26] + no_new_trades（不吃单） |

确认类任务（A02/A06/A07/A08/A09/A10）user_script 提供一条 approved。

## `oh run` 契约（本包落地 CLI）

`oh run --model <m> [--family a,b,c] [--env mock] [--task ID] [--out DIR] [--root DIR]`
- 任务筛选：family ∈ 列表 ∧ (task.env == env ∨ task.env == both) ∧ (--task 时 id 精确匹配)；
- provider：`scripted` → 读 `scripts/<ID>.yaml` 动作序列，缺省回放 report(blocked)；
  其他 → LiteLLMProvider（温度 0）；
- 指纹：model / model_version（litellm 响应回填）/ taskset_version("v0.1.0") /
  git_commit（rev-parse，失败 "unknown"）/ UTC 时间戳 / temperature "0"（R11）；
- 落盘：`<out>/<task_id>.json`（save_result，R2 脱敏）+ `<out>/meta.json`；
  默认 out = `results/<model>-<UTC 时间戳>/`；
- M1 过渡形态：scoring=null，`oh score`（FP08）回填（Q1(a)）；
- `--env testnet` 在 FP11 前明确报错（不静默）。

## AC 与测试

specs/00 · FP06 的 AC-06a–e；`tests/test_tasks_a.py`、`tests/test_e2e.py`、`tests/test_cli.py`。
