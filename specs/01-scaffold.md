# specs/01 — FP01 项目脚手架与领域模型（M1）

> 对应 specs/00-milestones.md · FP01。守护红线：R1 R2 R9 R11（+ 5.2 D-a 结构性约束）。

## 目标

可 `uv sync && uv run pytest` 的最小仓库骨架 + 全项目共用的领域模型与安全地基。
不实现任何工具/环境/评分行为（分属 FP03–FP08）。

## 模块布局（第 12 节目录树的 FP01 子集）

```
src/open_harness/
├── __init__.py          # __version__
├── money.py             # Money 类型：Decimal-only（R9）
├── secrets.py           # get_secret / redact（R2）
├── net.py               # check_url 交易所侧 URL 白名单（R1）
├── results.py           # Fingerprint + ResultRecord（R11）
├── cli.py               # typer app 骨架（子命令随各 FP 落地）
├── tasks/schema.py      # TaskSpec / MandateSpec / AssertionSpec / UserScriptRule
├── env/fixture.py       # FixtureSpec（第 10 节字段）
└── {env,tools,agent,scoring,report}/__init__.py
```

## 设计定案

- **Money**：`Annotated[Decimal, BeforeValidator, PlainSerializer(str)]`。接受 str/int/Decimal，
  **拒绝 float 与 bool**（R9）；JSON 序列化为字符串（回放字节确定性，Q5）。
- **net.check_url**：交易所侧白名单硬编码 `{testnet.binance.vision}`；主网域名 raise `ForbiddenHostError`。
  模型侧不走此层（D-a），改结构性约束——HTTP/模型 SDK 只允许 `agent/providers.py`、`scoring/judge.py` import，
  ccxt 只允许 `env/testnet.py` import；测试名定案：`test_r1_model_calls_only_from_provider_modules`。
- **secrets**：key 只经 `os.environ`（`OH_TESTNET_API_KEY/SECRET`）；`redact()` 扫描环境中
  名称含 KEY/SECRET/TOKEN/PASSWORD 的值（长度 ≥6）做替换，供日志/结果落盘前调用（FP05 接线）。
- **Fingerprint**：model / model_version / taskset_version / git_commit / timestamp / temperature 全必填；
  temperature 存字符串（非资金数字，但避免 float 进结果 JSON）。
- **ResultRecord**：task_id + status(done/blocked/max_steps/timeout/infra_error) + fingerprint 必填；
  trajectory/final_state/scoring 先留宽类型，FP05/FP07/FP08 收紧（M1 过渡形态 scoring=None，Q1(a)）。
- **AssertionSpec**：YAML 键 `assert` 是 Python 关键字 → 字段 `kind` + alias `"assert"`；
  合法断言类型全集（7.2 节）以 `FINAL_STATE_ASSERTIONS` / `TRAJECTORY_ASSERTIONS` 常量定义，FP02 validate 消费。
- **R9 lint 范围**：AST 扫描 `src/open_harness` 全部模块，禁止 float 字面量与 `float()` 调用；
  豁免名单 = `report/`（matplotlib 坐标）与 `agent/providers.py`（litellm temperature 参数）。
- KICKOFF 文档含非公开背景（1.4），**加入 .gitignore 不入仓库**；specs/ 为仓库内事实源。

## AC 与测试（同 specs/00 FP01 条目）

| AC | 测试 |
|----|------|
| AC-01a | `tests/test_scaffold.py::test_package_importable_and_layout` |
| AC-01b | `tests/test_schemas.py::test_money_fields_reject_float`、`::test_yaml_string_to_decimal` |
| AC-01c | `tests/test_redlines.py::test_r11_result_schema_requires_fingerprint` |
| AC-01d | `tests/test_redlines.py::test_r1_url_whitelist_blocks_mainnet` |
| AC-01e | `tests/test_redlines.py::test_r2_no_secrets_in_repo_files`、`::test_r2_api_key_only_from_env` |
| AC-01f | `tests/test_redlines.py::test_r9_no_float_in_money_paths` |
| D-a | `tests/test_redlines.py::test_r1_model_calls_only_from_provider_modules` |

依赖声明：pydantic v2、PyYAML、typer；dev：pytest。litellm/ccxt/mcp/matplotlib 随各自 FP 引入，保持地基轻量。
