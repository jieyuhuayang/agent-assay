# CLAUDE.md — AgentAssay

本仓库采用规格驱动开发（SDD）。最高事实来源：specs/ 目录（源头为 KICKOFF 文档）。

## 工作纪律
1. 实现任何特性前，先在 specs/00-milestones.md 找到对应特性包与 AC；无对应 AC 的代码不写。
2. 测试先行：AC → 测试名 → 实现；合并前该特性包测试全绿并勾选清单。
3. 12 条架构红线（specs/00 顶部）不可协商；触碰前必须停下询问 owner。
4. 金额一律 Decimal；工具 schema 只在 tools/registry.py 定义；judge 不得推翻断言。
5. 发现规格间矛盾：停下提问，不自行裁决；提问时给出你建议的解决方案。
6. commit 规范：feat|fix|test|docs(scope): 摘要；一个特性包一串连续 commit。

## 常用命令
uv sync / uv run pytest / uv run assay validate / uv run assay run --env mock --model scripted
