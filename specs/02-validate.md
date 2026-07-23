# specs/02 — FP02 任务/fixture/mandate 加载与 `assay validate`（M1）

> 对应 specs/00-milestones.md · FP02。守护红线：R5 R6（+ R9 数据面 lint）。依赖 FP01。

## 目标

YAML → pydantic 的加载层 + 全量 lint 命令 `assay validate`。产出首份 mandate（std_conservative）。

## 设计定案

- `tasks/loader.py`：`load_task` / `load_mandate` / `load_fixture`。mandate 文件支持顶层
  `mandate:` 包裹（KICKOFF 第 6 节样式）或直接平铺，加载时解包。
- `tasks/validate.py`：`validate_repo(root) -> ValidationReport{issues, 计数}`。检查项：
  1. **schema**：三类 YAML 分别过 TaskSpec / FixtureSpec / MandateSpec；YAML 语法错误同报 schema；
  2. **ref**：task.fixture / task.mandate 相对 repo root 必须存在；
  3. **r5**：`final_state + trajectory` 至少 1 条程序断言，禁止 judge-only（红线 R5）；
  4. **assert-kind**：断言 kind 必须属于 7.2 节全集，且终态/轨迹两组不得放错区
     （常量来自 `tasks/schema.py`，与 FP07 引擎共用一份清单）；
  5. **float**：YAML 数据里出现裸 float（未加引号的小数）即报错（R9 数据面：数字必须写成字符串）；
  6. **r6**：任务/fixture/mandate 原文扫描「可操作化语料」——真实链上地址模式
     （ETH `0x`+40hex、BTC base58/bech32、TRON T+base58）、私钥格式（64 hex）、
     助记词模式（≥12 连续 BIP39 词）。mock 地址必须设计为**不匹配**这些模式
     （如 `TMockWhitelisted…` 含 base58 非法字符 `l`）。
- CLI：`assay validate [--root PATH]`。任一 issue → 非零退出并逐条打印 `[code] file: message`；
  全绿打印计数摘要。空任务集不是错误（任务随 FP06/FP09 入库）。
- 仓库播种：`tasks/{a,b,c}/.gitkeep`、`mandates/std_conservative.yaml`（第 6 节内容，mock 地址）。

## AC 与测试（同 specs/00 FP02 条目）

| AC | 测试 |
|----|------|
| AC-02a | `tests/test_loader.py::test_task_yaml_roundtrip` |
| AC-02b | `tests/test_validate.py::test_missing_reference_fails` |
| AC-02c | `tests/test_redlines.py::test_r5_validate_rejects_judge_only_task` |
| AC-02d | `tests/test_redlines.py::test_r6_validate_flags_operational_redteam_corpus` |
| AC-02e | `tests/test_validate.py::test_unknown_assertion_type_rejected` |
| AC-02f | `tests/test_cli.py::test_validate_exit_codes`（含对真实仓库 root 跑 validate 必须绿） |
