# 评测框架选型调研：promptfoo

> 调研日期 **2026-07-30**；对象 [promptfoo](https://github.com/promptfoo/promptfoo)
> （main 分支 + npm latest `0.121.19`，发布于 2026-07-14）。
>
> 起因：评估 AgentAssay 是否应改用 promptfoo 作为评测框架（含全量改写的可能）。
>
> **结论：不改用。** 未动任何代码。可选的部分采纳路径见 §6，均不进 CI 运行时。
>
> 取证方式：多 agent 检索 → 抓取 23 个一手来源 → 抽取 114 条 claim → 对其中 25 条
> 做 3 票对抗验证（需 2/3 反驳才判死）→ 9 条确认、16 条驳回。承重结论另经人工复核
> （见 §7 的复核记录）。本文所有关键论断均附可核查出处；未取证的维度在 §8 显式列出，
> 不得当作已核查结论引用。

---

## 1. 结论

| 问题 | 结论 |
|------|------|
| 是否改用 promptfoo 作为评测框架 | **否** |
| 是否存在部分采纳价值 | 有，但有限；仅限离线试用（§6） |
| 是否引入为运行时/CI 依赖 | **否** |
| 若将来确要换 harness，对标目标 | 不是 promptfoo，应先评估 Inspect AI（未取证，§8） |

一句话理由：迁移会把 4800 行 Python 引擎从**宿主**降为**被 Node 编排的子进程**，
同时把 R3（judge 不得推翻程序断言）从结构性保证降级为一句约定——用一条架构红线换一个
框架，净收益为负。

---

## 2. 必须先纠正的一个前提

调研前的直觉判断是「promptfoo 只是 prompt→输出文本评测器，评不了 agent」。
**这个前提是错的，不能作为不迁移的理由。**

promptfoo 有一族一等公民的轨迹断言：`trajectory:tool-used` / `trajectory:tool-args-match` /
`trajectory:tool-sequence` / `trajectory:step-count` / `trajectory:goal-success`。

- 类型名逐字存在于 `src/types/index.ts:651-655`（`BaseAssertionTypesSchema`），
  在 `src/assertions/index.ts:143-147` 注册、306-310 行映射到 `handleTrajectory*` 实现；
- 已排除「文档超前于发布」：npm `promptfoo@0.121.19` tarball 中
  `grep 'trajectory:tool-used' package/dist` 命中 594 处；
- 官方文档逐字：「The `trajectory:tool-used` assertion checks traced tool steps rather
  than the model's final output.」
  （<https://www.promptfoo.dev/docs/configuration/expected-outputs/deterministic/>）
- 取反不需专门枚举：`NotPrefixedAssertionTypesSchema` 对全部 base type 自动生成 `not-`
  前缀，运行时在 `src/assertions/index.ts:354/365` 以 `startsWith('not-')` / `slice(4)` 分派。

即：AgentAssay 的 `call_order` / `tool_not_called` 在 promptfoo 里**是有第一方对应物的**。

本轮共有 16 条「promptfoo 只能评文本 / 没有工具执行器 / 断言上下文没有轨迹入口 /
设计哲学是声明式无代码」的直觉性 claim 被 0-3 或 1-2 驳回。**不迁移的理由必须建立在
§3 的四条结构性错配上，而非「它评不了 agent」。**

---

## 3. 四条结构性错配

### 3.1 R3 被打穿（决定性）

promptfoo 把全部断言（含 `llm-rubric` / `g-eval`）按 weight 混入同一个加权平均池，
一旦设 threshold，test 的 pass/fail 由合并总分决定，直接覆盖单条断言的否决。

| 证据 | 内容 |
|------|------|
| 源码 | `src/assertions/assertionsResult.ts:96-97`：`totalScore += result.score * weight`，**对断言类型零分支** |
| 源码注释 | 逐字：「A numeric test threshold overrides the pass/fail status of individual assertions.」（`testResult()`，149-161 行 `pass` 被重新赋值而非与原值取与） |
| 官方自带单测 | `test/assertions/assertionsResult.test.ts:136-171`：一条 `{pass:false, score:0}` + 一条 `{pass:true, score:1}`，threshold 0 → **`pass=true`**、score 0.5、reason「Aggregate score 0.50 ≥ 0 threshold」 |
| 文档 | 「A threshold of 0 makes the test case pass regardless of individual assertion failures」 |

把上面那条通过的断言换成连续 0-1 打分的 `llm-rubric`，就是 R3 被击穿的场景。
附带风险：`weight: 0` 会让断言「automatically passes」，**一条程序断言可被静默中和**。

逃生阀有两个，都不够：

- `assert-set` 分组 threshold——部分有效，外层 test 若也设 threshold 仍被外层聚合覆盖；
- `assertScoringFunction`——签名 `(namedScores, context) => GradingResult`，结果以
  `{...this.result, ...scoringResult}` 展开覆盖，**可无条件硬否决**。但 promptfoo 官方文档
  自己把「Failing if any critical metric falls below a threshold」列为该函数的*使用场景*，
  等于承认这是用户代码而非框架不变量。

对照现状：AgentAssay 的 R3 是靠「judge 输出类型里根本没有 pass/fail 字段」这一**类型层
结构保证**实现的（`tests/test_redlines.py::test_r3_judge_output_type_has_no_passfail`），
`scoring/pipeline.py:99` 的 `passed` 只有 `evaluate_assertions` 一个来源。迁移后这条保证
只能靠自写函数 + 守护测试补回。

还有一层更细的损失：即便不设 threshold（默认 all-must-pass，即风险是 opt-in），
promptfoo 输出的 score 也**始终是被 judge 污染的混合分**，不存在「硬不变量分 / 质量分」
分离——而这正是 AgentAssay「judge 只给 0-2 质量分、不参与 pass 判定」的设计意图。

### 3.2 轨迹断言绑定 OTel span，且看不见环境终态

`trajectory:*` 的数据源是 OpenTelemetry span，不是 harness 自持的轨迹对象。

- 五个 handler 第一句都是 `getTraceOrThrow`（`src/assertions/trajectory.ts:41-47`），
  无 trace 直接 throw；
- `extractTrajectorySteps(trace)` 只遍历 `trace.spans`，**没有任何回退到
  `providerResponse` / `tool_calls` / message 数组的路径**；工具名从 span attribute
  （`tool.name` / `gen_ai.tool.name` / `function.name` / `ai.toolCall.name`）提取；
- tracing 文档明确「Promptfoo automatically instruments its **built-in** providers」——
  自定义 provider 不在其列；
- OpenAI Agents 指南逐字：「Promptfoo can only assert on tool paths if it receives the
  agent's internal spans」「If you skip this exporter, Promptfoo will not see the SDK's
  tool and handoff spans, so `trajectory:*` assertions will not have the trace data they need」。

代价：mock 撮合 / x402 环境需新增 OTel SDK 依赖并自写埋点（需解析 promptfoo 注入的
W3C `traceparent`、转 OTLP JSON 上报）。减轻项：promptfoo 自带 OTLP receiver
（`tracing.enabled` + `otlp.http.enabled`，端口 4318，「No external collector required
for basic usage」），成本主要在 provider 侧。

**但埋完也没用在刀刃上**：`AssertionValueFunctionContext` 的字段是
`prompt / vars / test / logProbs / config / provider / providerResponse / trace / metadata`,
**没有任何环境终态入口**。span 表达不了撮合账本余额、挂单、支付流水。

本仓库现状对照（`grep -rn 'assert:' tasks/`，48 任务共 **173** 条断言）：

| 断言 | 条数 | 作用对象 |
|------|-----:|---------|
| `tool_called` | 42 | 轨迹 |
| `report_answer` | 28 | 轨迹（`report` 工具调用的 `answer` 参数，非自由文本） |
| `balance` | 22 | **环境终态** |
| `no_new_trades` | 18 | **环境终态** |
| `confirm_before_irreversible` | 14 | 轨迹（存在量词） |
| `tool_not_called` | 11 | 轨迹 |
| `payments_within` | 9 | **环境终态** |
| `spend_within` | 4 | **环境终态**（跨调用累计） |
| `order_state` | 4 | **环境终态** |
| `open_order_exists` / `open_order_absent` | 3 / 3 | **环境终态** |
| `no_payments` | 3 | **环境终态** |
| `max_semantic_errors` / `call_order` | 3 / 3 | 轨迹 |
| `no_forbidden_payee` / `no_forbidden_address` | 2 / 2 | 轨迹 |
| `clarify_before_action` | 2 | 轨迹 |

其中 **66 条查环境终态**（promptfoo 无任何原生入口）、**107 条查轨迹**；**0 条**作用于模型自由文本输出——
而后者是 promptfoo 断言体系覆盖最好的部分。能力重叠区与需求区基本错开。

### 3.3 内置工具/轨迹断言只做「工具名集合 + 字面参数相等」

| 证据 | 内容 |
|------|------|
| 文档 | `tool-call-f1`：「unordered set comparison — only the presence of tool names matters, not the order or frequency」 |
| 源码 | `src/assertions/toolCallF1.ts` 的 `extractToolNames` 只把 `fn.name` / `block.name` 塞进 `Set<string>`，**参数从不读取** |
| 源码 | `trajectory:tool-args-match` 的比较终点是 `isDeepStrictEqual`（exact）或 `matchesExpectedArgsPartial` 递归后仍落到 `isDeepStrictEqual`（partial） |
| 源码 | 在 `trajectory.ts` 中 grep `RegExp|regex|gte|sum|reduce` **零命中**；唯二旋钮 `defaults` 与 `ignore` 仍是字面相等 |
| 源码 | 内置工具/轨迹断言全集（`types/index.ts:627-655`）中**无一对参数值做算术**；`step-count` 只对步数做 min/max |

因此以下断言**不能声明式表达**，必须回落到 custom python 断言自行遍历原始 span：

- `spend_within` — 跨调用累计 Decimal 花费；
- `qty_step_aligned` — 步长取模；
- `no_forbidden_address` / `no_forbidden_payee` — 白名单否定谓词；
- `confirm_before_irreversible` — 存在量词（∃ j<i 的确认调用）。

两条必须携带的限定（避免把结论说过头）：

- (a) 不可外推成「promptfoo 表达不了 `spend_within`」——`TRACE_AWARE_ASSERTION_TYPES`
  含 `'python'` / `'javascript'`，`assertionMayNeedTraceContext()` 会把 `context.trace`
  （含 spans）注入自定义断言，故 custom python 断言**可以**自行遍历 span 累加。
  但拿到的是**原始 span**；归一化后带 args 的 `TrajectoryStep` 是 `trajectoryUtils`
  内部结构，不暴露给用户代码。
- (b) `qty_step_aligned` 这类单参数步长约束理论上可借 `is-valid-openai-tools-call` 的
  Ajv `multipleOf` 表达，但该 schema **同时就是发给模型的工具定义**，约束会泄漏进 prompt
  （污染被测对象），且仍做不了跨调用累计。

### 3.4 没有 in-process Python SDK；Decimal 过不了进程边界

| 证据 | 内容 |
|------|------|
| Node API 参考页 | 只有 TypeScript 签名 `async function evaluate(testSuite: EvaluateTestSuite, options?): Promise<Eval>`，全页零 Python 绑定 |
| PyPI | 官方 `promptfoo` 包（v0.1.4, 2026-04-06）README 自述「a lightweight wrapper that installs promptfoo via pip. It requires Node.js 20+ and executes `npx promptfoo@latest` under the hood」——不暴露任何 Python 函数/类，只有 CLI entry point |
| 源码 | 断言侧：inline `python:` 走 `runPythonCode → runPython`；`file://xxx.py` 走 `src/assertions/index.ts:495-497` 的 `runPython`。`runPython`（`src/python/pythonUtils.ts:302-337`）每次调用建临时目录、写 `input.json`/`output.json`、`new PythonShell('wrapper.py')`、finally 删目录——**每条断言 fork 一个 OS 进程** |
| 源码 | `PythonWorkerPool` **只被** `src/providers/pythonCompletion.ts` 引用（382 行注释「Use worker pool instead of runPython」）；**断言路径完全不碰它** |
| 自托管 server | Express + web UI/results API，无 OpenAPI/REST 评测执行入口——不存在绕开 Node 的第三条路 |

对 R9（金额全程 Decimal）的影响：跨进程只有 JSON，JSON number 即 IEEE754 double。
保 Decimal 必须全程字符串并在 Python 侧手工还原，promptfoo 内置数值断言一个都用不上。

一个反向有用的推论：**provider 路径**有 persistent worker（官方文档「Python providers use
persistent worker processes. Your script is loaded once when the worker starts, not on every
call」，并建议 `workers: 1` 保持会话状态），所以有状态环境**可以**整体驻留在一个 python
provider worker 内。但任何 promptfoo 断言都拿不到该对象引用，只能收到序列化 JSON。
这正是 §6「外层 runner」形态的技术依据。

---

## 4. 七项架构特征逐条对照

| # | AgentAssay 特征 | promptfoo 对应能力 | 判定 |
|---|---|---|---|
| 1 | 有状态确定性 mock 环境 +「绝不硬拦截」（D3）+ testnet 集成 | 无此概念；环境须整体活在 python provider 内，promptfoo 只看得到 provider 返回 | 自写；引擎降为子进程 |
| 2 | 断言作用于完整轨迹 + 最终状态 | 轨迹侧有原生 `trajectory:*`（需 OTel 埋点、只做名称/字面匹配）；**终态侧无任何入口** | 部分原生 + 大量自写 |
| 3 | judge 只给 0-2，**不得推翻程序断言**（R3） | 加权平均 + threshold 覆盖单条断言 | **根本冲突** |
| 4 | 金额全程 Decimal（R9） | 断言侧跨进程 JSON | 自写 |
| 5 | 12 条架构红线（R1/R2/R4/R7/R8/R11 等） | 无机制承载这些不变量 | 自写 |
| 6 | MCP server 从同一 registry 反射 schema（R7） | 与 promptfoo 正交，迁移不改善 | 无变化 |
| 7 | 排行榜 + 雷达图 SVG | 有 web UI 与多 provider 对比 | **唯一可能净收益项**（自定义图表能力未取证） |

---

## 5. 治理风险：promptfoo 已被 OpenAI 收购

官方博文（2026-03-09）逐字：

> Today we are announcing that Promptfoo has agreed to be acquired by OpenAI.

> Promptfoo will remain open source and we will continue to serve users and customers.

来源：<https://www.promptfoo.dev/blog/promptfoo-joining-openai/>（本条已人工复核，见 §7）。
公告承诺开源延续，但**未说明适用的具体 license、治理结构、仓库归属或功能变更时间表**。

这对 AgentAssay 不是泛泛的厂商锁定风险，而是与本项目已有决策直接冲突：

> specs/00 决策记录（2026-07-25）：judge = `kimi/kimi-k3`（DashScope；**与三运动员不同家，
> 避免同门偏袒**）。

同一条逻辑必须适用于 harness 本身。一个要发布**跨厂商模型排行榜**的基准，跑在某一家
模型厂商拥有的评测框架上，构成本项目自己已经拒绝过的偏见来源。这一点独立于任何技术
理由，单独就足以否决把 promptfoo 引入运行时。

---

## 6. 可选的部分采纳路径（均为离线试用，不进 CI）

| 路径 | 技术可行性 | 建议 |
|------|-----------|------|
| **红队语料生成** — Node API 导出 `redteam.{generate, run, Plugins, Strategies, Extractors, Base.Plugin, Base.Grader}`，可单独调用 | 有据 | 可试。**一次性离线导出**，对 C 族社工/注入语料做小样本适配度评估。生成物必须过 `assay validate`（R6 三原则：无真实地址/私钥格式、无可复用钓鱼模板、绑定本环境 mock 实体），见 `docs/redteam-review.md` |
| **web 报告 UI** | 有 web UI 与多 provider 对比 | 收益存疑。已有排行榜 + 雷达图 SVG，且其 UI 能否承载雷达图等自定义图表**未取证** |
| **把现有引擎包成 python provider 作外层 runner** | 有据（persistent worker + `workers: 1`） | **不建议**。断言逻辑要么折叠进 provider（那 promptfoo 只剩一个 CLI 外壳），要么经 metadata 序列化外送（丢 Decimal、丢环境对象），同时引入 Node.js 20+ 硬依赖，并继承 §3.1 的打分模型冲突 |

通用风险：promptfoo 已发布 417 个版本，迭代极快；`trajectory:*` 与 `assertScoringFunction`
均属较新特性，接口在数月尺度上可能变化。

---

## 7. 人工复核记录

自动调研的两条承重结论被单独复核（2026-07-30）：

| # | 待核事实 | 复核结果 |
|---|---------|---------|
| 1 | 「OpenAI 收购 promptfoo」——自动调研中仅为旁证、被标为需独立核实 | **成立**。抓取官方博文原文，两句承诺均逐字确认（§5） |
| 2 | 「promptfoo 官方声明不模拟完整有状态环境（no full stateful environment simulation）」——来自检索摘要 | **不成立，已剔除**。抓取 <https://www.promptfoo.dev/docs/red-team/agents/> 原文，页面并无此表述。该页实际给出三层测试路径（黑盒 endpoint / 自定义 provider 挂内部函数 / OTel trace 观测），三者均指向「被测系统与其环境在 promptfoo 之外」，这一点与本文结论一致，但**不能引用为官方否认性声明** |

第 2 条的处理方式记录在此，是为了防止后续引用时把一句未经证实的表述当作官方立场。

---

## 8. 未取证边界（不得当作已核查结论引用）

本轮以下维度**无存活的取证 claim**，§1–§6 的论断均未依赖它们：

1. **promptfoo 的确定性与可复现能力** — 是否有模型级 seed、结果环境指纹（模型版本 /
   数据集版本 / git commit）、不重跑模型的离线重打分（`TestCase.providerOutput` 能否承载
   完整多轮轨迹而非只有最终输出）。这直接决定 **R4**（相同 seed+fixture+scripted 字节一致
   回放）与 **R11**（结果文件必含环境指纹）能否被承载。相关候选 claim 被 0-3 驳回且无替代证据。
2. **报告与可视化的自定义能力** — web UI 能否承载雷达图等自定义图表（§4 第 7 项、§6）。
3. **许可证与企业版功能墙的具体边界** — 开源核心与 enterprise 之间的划线（web UI / share /
   team / redteam 高级策略是否收费或默认走云端）；收购后条款是否已变化。
4. **是否存在真正的内置 tool executor** — provider 级 `functionToolCallbacks`、MCP provider、
   redteam 的 crescendo/goat 多轮策略能否驱动自定义有状态 Python 环境完成多轮闭环。
   本轮只能安全断言「文档化的核心执行模型不含工具执行闭环」（`docs/configuration/tools/`
   的 4 步流程中，step 3 标题即「**Your code** executes the function」），
   更强版本「promptfoo 完全没有内置 tool executor」被 0-3 驳回。
5. **同形态框架横向对照** — τ-bench / AgentDojo / Inspect AI / DeepEval / LangSmith /
   Braintrust 均未取证。其中值得单列的方向：**若将来确要更换 harness，对标目标不是
   promptfoo，而是 Inspect AI（UK AISI）**——Python 原生、自带 solver/scorer 与 sandbox，
   形态上比 promptfoo 接近得多。此判断本身**未取证**，需专项调研。
6. **社区迁移先例** — 是否有把有状态环境 + 轨迹断言引擎迁入 promptfoo 的公开案例。

另需注意证据性质：几乎全部证据来自 promptfoo 自家文档与仓库源码。源码与自带单测属可独立
复核的强证据，但「无某功能」类否定性结论只能证到「官方文档与 main 分支未见」，不能排除
未文档化路径。

---

## 9. 主要来源

一手（官方文档 / 源码 / 发布产物）：

- <https://www.promptfoo.dev/docs/configuration/expected-outputs/>
- <https://www.promptfoo.dev/docs/configuration/expected-outputs/deterministic/>
- <https://www.promptfoo.dev/docs/configuration/expected-outputs/python/>
- <https://www.promptfoo.dev/docs/configuration/tools/>
- <https://www.promptfoo.dev/docs/configuration/reference/>
- <https://www.promptfoo.dev/docs/tracing/>
- <https://www.promptfoo.dev/docs/providers/python/>
- <https://www.promptfoo.dev/docs/providers/simulated-user/>
- <https://www.promptfoo.dev/docs/integrations/python/>
- <https://www.promptfoo.dev/docs/usage/node-api-reference/>
- <https://www.promptfoo.dev/docs/guides/evaluate-openai-agents-python/>
- <https://www.promptfoo.dev/docs/red-team/agents/>
- <https://www.promptfoo.dev/docs/red-team/strategies/multi-turn/>
- <https://www.promptfoo.dev/docs/enterprise/>
- <https://www.promptfoo.dev/blog/promptfoo-joining-openai/>
- <https://pypi.org/project/promptfoo/>（v0.1.4）
- promptfoo 源码：`src/types/index.ts`、`src/assertions/index.ts`、`src/assertions/trajectory.ts`、
  `src/assertions/trajectoryUtils.ts`、`src/assertions/toolCallF1.ts`、
  `src/assertions/assertionsResult.ts`、`src/assertions/python.ts`、
  `src/python/pythonUtils.ts`、`src/python/workerPool.ts`、`src/providers/pythonCompletion.ts`、
  `src/providers/openai/util.ts`
- promptfoo 自带单测：`test/assertions/assertionsResult.test.ts`
- npm 发布产物：`promptfoo@0.121.19` tarball `package/dist`

---

## Owner 裁决

- [ ] 已阅本文，确认维持自研 harness，不引入 promptfoo（裁决人 / 日期：____ / ____）
- [ ] 是否批准 §6「红队语料生成」离线试用：□ 批准 □ 不批准
- [ ] 是否批准 §8 第 5 项「Inspect AI 专项调研」：□ 批准 □ 不批准

维护规则：promptfoo 迭代极快（417 个版本），本文结论绑定 `0.121.19` / main@2026-07-30。
若将来重启该议题，§8 的六项空白应优先补齐，尤其第 1 项（R4/R11 承载能力）。
