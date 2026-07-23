# specs/12 · FP12 报告、三模型跑分与发布（M3）

> 上游：specs/00 §FP12（AC-12a–12f）、KICKOFF §9.3（报告卡）、§15（README 结构）。
> 红线接触面：R12（免责声明）、R9（比率 Decimal 字符串；matplotlib 坐标是 R9 豁免区 `report/`）。

## 1. 范围

- `report/build.py`：run 目录 → `TaskScore` 行 → `compute_metrics` → leaderboard 表 +
  六维雷达 SVG + `report.md`；
- `assay report <run_dir...> [--out] [--root]`：多 run 对比（每 run = 一个模型一次全量跑分）；
- 双语 README（§15 结构，R12 声明）；`docs/sample-report/`（AC-12d 三模型跑分后入库）。

## 2. 行组装（AC-12a 的对账面）

每个结果 JSON → `TaskScore`：

| TaskScore 字段 | 来源 |
|---|---|
| task_id / status / passed | record 顶层 + `scoring.passed` |
| family / tags | `root/tasks/<族>/<id>.yaml`（tags 是 D-c 分母；task 文件缺失 → fail loud） |
| tool_calls…clarified | `scoring.stats`（FP08 已产出，报告层不重算） |
| judge_quality | `scoring.judge.quality`（无 judge → None） |
| wall_ms | `timing.wall_ms` |
| tokens | `timing.tokens.prompt_tokens + completion_tokens`（无 usage → None） |

- `scoring.mode == "structural"`（testnet run）→ **拒收**（fail loud）：D1 规定正式跑分只在
  mock，结构评分结果混入 leaderboard 即数据事故；
- run 标签 = `meta.json` 的 `model`（缺省用目录名）；同名标签自动唯一化（`x`→`x#2`），
  SVG 文件名 slug 冲突自动加后缀，调色板超 7 个 run 循环使用——多 run 对比不得静默
  覆盖/丢失（M3 审查修复）。

## 3. 雷达图（AC-12b，KICKOFF 9.3 六维）

轴（顺序固定；SVG 文本可检索——`svg.fonttype="none"`，轴标签用英文避免 CJK 字体缺字）：

1. `A success` = task_success_rate.by_family.a
2. `B success` = by_family.b
3. `C safety` = mean(1−Unsafe, 1−Overreach)（缺失方不计入均值；两者全缺 → None）
4. `Tool accuracy` = tool_calling_accuracy
5. `Clarification` = mean(Clarification, 1−Over-refusal)（同上缺失口径）
6. `Efficiency` = min(cost)/cost（跨 run 成本归一；cost 基准：**全部** run 都有
   tokens_mean 用 tokens，否则全部用 wall_ms_mean——不许混用两种量纲；单 run 恒 1）

- 值域 [0,1]；指标 None → 轴画 0 并在 report.md 注明「未测出（分母为 0）」；
- 输出：`radar-<label>.svg` 每模型一张 + `radar-overlay.svg` 叠加一张（单 run 也生成，
  形态与多 run 一致）；配色 Okabe-Ito（色盲友好）；比率在绘图边界转 float
  （`report/` 是 R9 豁免区，唯一允许处）。

## 4. report.md 结构（AC-12c）

标题（含 taskset_version / git_commit / 生成条件）→ Leaderboard 表
（`Model | Overall | A | B | C | Unsafe | Overreach | Over-refusal | Mean cost`，
None → `—`，数字**原样引用** compute_metrics 的 Decimal 字符串——AC-12a 对账即
「表中数字 == 结果 JSON 重新组装后 compute_metrics 的输出」）→ 雷达图内嵌链接 →
逐模型指标明细（九项全量 JSON 摘要）→ **R12 免责声明**（中英双语常量
`DISCLAIMER_EN/ZH`，README 复用同款措辞）。

## 5. 测试映射

| AC | 测试 | 要点 |
|----|------|------|
| AC-12a | `test_report.py::test_leaderboard_reconciles_with_results` | 合成 run 目录（真实 tasks/ 提供 tags）→ 解析 report.md 表格 → 与按原始 JSON 独立重算的期望值逐格相等（含 infra_error 剔除、None → `—`） |
| AC-12b | `test_report.py::test_radar_six_axes_svg` | 双 run → 每模型 SVG + overlay 存在；SVG 文本含全部六个轴标签 |
| AC-12c | `test_redlines.py::test_r12_report_and_readme_contain_disclaimer` | 生成的 report.md 与仓库 README.md / README.zh-CN.md 均含「not investment advice / 非投资建议 / 真实资金」关键短语 |
| AC-12d | 【人工】≥3 模型 × 36 任务，样例报告入 `docs/sample-report/` | **被 Q2 阻塞**（模型选型 + API key） |
| AC-12e | 【人工】双语 README §15 结构 + 关键发现 3 条 | 结构与初稿随本包入库；关键发现待 AC-12d 跑分后提炼 |
| AC-12f | 【人工】发布检查清单 + 转 public | LICENSE / secret 扫描 / pytest 全绿 / Quickstart 15 分钟 / R6 已签核 / 图渲染 / R12 |
