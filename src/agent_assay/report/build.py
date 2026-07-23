"""报告构建（FP12，specs/12）：run 目录 → leaderboard + 六维雷达 SVG + report.md。

R9 说明：`report/` 是 float 豁免区（matplotlib 坐标边界）——比率只在绘图前的
最后一刻转 float；一切进 report.md 的数字原样引用 compute_metrics 的 Decimal 字符串。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from ..results import ResultRecord
from ..scoring.metrics import TaskScore, compute_metrics
from ..tasks.loader import load_task

_Q4 = Decimal("0.0001")

DISCLAIMER_EN = (
    "**Disclaimer**: all results come from a deterministic **mock exchange** (default) or the "
    "Binance Spot **Testnet** (fake funds). This project is a research benchmark — "
    "**not investment advice**; never use it with real funds."
)
DISCLAIMER_ZH = (
    "**免责声明**：所有结果产自确定性 **mock 交易所**（默认）或假资金的 Binance Spot "
    "**Testnet**。本项目仅为研究基准——**非投资建议**，请勿用于真实资金。"
)

RADAR_AXES = [
    "A success", "B success", "C safety", "Tool accuracy", "Clarification", "Efficiency",
]

# Okabe-Ito 色盲友好配色
_PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442"]


@dataclass
class RunReport:
    label: str
    run_dir: Path
    rows: list[TaskScore]
    metrics: dict[str, Any]


# ------------------------------------------------------------ 行组装 ----


def _row_of(record: ResultRecord, root: Path) -> TaskScore:
    scoring = record.scoring or {}
    if scoring.get("mode") == "structural":
        raise ValueError(
            f"{record.task_id}: testnet 结构评分结果不得进入报告（D1：正式跑分只在 mock）"
        )
    task = load_task(root / "tasks" / record.task_id[0].lower() / f"{record.task_id}.yaml")
    stats = scoring.get("stats") or {}
    judge = scoring.get("judge") or {}
    timing = record.timing or {}
    tokens = timing.get("tokens") or None
    tokens_total = (
        tokens.get("prompt_tokens", 0) + tokens.get("completion_tokens", 0) if tokens else None
    )
    return TaskScore(
        task_id=record.task_id,
        family=task.family,
        tags=task.tags,
        status=record.status,
        passed=scoring.get("passed"),
        tool_calls=stats.get("tool_calls", 0),
        schema_errors=stats.get("schema_errors", 0),
        semantic_errors=stats.get("semantic_errors", 0),
        hallucination_calls=stats.get("hallucination_calls", 0),
        unsafe=bool(stats.get("unsafe")),
        overreach=bool(stats.get("overreach")),
        clarified=bool(stats.get("clarified")),
        judge_quality=judge.get("quality"),
        wall_ms=timing.get("wall_ms"),
        tokens=tokens_total,
    )


def load_run(run_dir: Path, root: Path) -> RunReport:
    meta: dict[str, Any] = {}
    meta_path = run_dir / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rows = []
    for path in sorted(run_dir.glob("*.json")):
        if path.name == "meta.json":
            continue
        record = ResultRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        rows.append(_row_of(record, root))
    if not rows:
        raise ValueError(f"{run_dir} 下没有结果文件")
    return RunReport(
        label=str(meta.get("model") or run_dir.name),
        run_dir=run_dir,
        rows=rows,
        metrics=compute_metrics(rows),
    )


# ------------------------------------------------------------ 六维值 ----


def _dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _mean_available(parts: list[Decimal | None]) -> Decimal | None:
    known = [p for p in parts if p is not None]
    if not known:
        return None
    return (sum(known) / len(known)).quantize(_Q4, ROUND_HALF_EVEN)


def efficiency_by_label(runs: list[RunReport]) -> dict[str, Decimal | None]:
    """成本归一（specs/12 §3 轴 6）：全部 run 有 tokens_mean 用 tokens，否则统一 wall_ms_mean。"""
    def cost(run: RunReport, key: str) -> Decimal | None:
        return _dec(run.metrics["cost_latency"].get(key))

    key = "tokens_mean" if all(cost(r, "tokens_mean") is not None for r in runs) else "wall_ms_mean"
    costs = {r.label: cost(r, key) for r in runs}
    known = [c for c in costs.values() if c is not None and c > 0]
    if not known:
        return {label: None for label in costs}
    best = min(known)
    return {
        label: (best / c).quantize(_Q4, ROUND_HALF_EVEN) if c is not None and c > 0 else None
        for label, c in costs.items()
    }


def radar_values(metrics: dict[str, Any], efficiency: Decimal | None) -> list[Decimal | None]:
    by_family = metrics["task_success_rate"]["by_family"]
    unsafe = _dec(metrics["unsafe_action_rate"]["rate"])
    overreach = _dec(metrics["overreach_rate"]["rate"])
    clarification = _dec(metrics["clarification_rate"]["rate"])
    over_refusal = _dec(metrics["over_refusal_rate"]["rate"])
    return [
        _dec(by_family.get("a")),
        _dec(by_family.get("b")),
        _mean_available([
            Decimal(1) - unsafe if unsafe is not None else None,
            Decimal(1) - overreach if overreach is not None else None,
        ]),
        _dec(metrics["tool_calling_accuracy"]),
        _mean_available([
            clarification,
            Decimal(1) - over_refusal if over_refusal is not None else None,
        ]),
        efficiency,
    ]


# ------------------------------------------------------------ 绘图 ----


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "model"


def _render_radar(
    path: Path, series: list[tuple[str, list[Decimal | None]]]
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["svg.fonttype"] = "none"  # SVG 里保留文本（AC-12b 可检索）
    angles = [n / len(RADAR_AXES) * 2 * math.pi for n in range(len(RADAR_AXES))]
    closed_angles = angles + angles[:1]
    fig = plt.figure(figsize=(6, 6))
    ax = fig.add_subplot(polar=True)
    for (label, values), color in zip(series, _PALETTE):
        floats = [float(v) if v is not None else 0.0 for v in values]  # R9 豁免区唯一转换点
        ax.plot(closed_angles, floats + floats[:1], label=label, color=color, linewidth=2)
        ax.fill(closed_angles, floats + floats[:1], color=color, alpha=0.12)
    ax.set_xticks(angles)
    ax.set_xticklabels(RADAR_AXES)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.legend(loc="lower right", bbox_to_anchor=(1.15, -0.1), fontsize=9)
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


# ------------------------------------------------------------ 汇总 ----


def _cell(value: Any) -> str:
    return "—" if value is None else str(value)


def _leaderboard_table(runs: list[RunReport]) -> str:
    lines = [
        "| Model | Overall | A | B | C | Unsafe | Overreach | Over-refusal | Mean cost |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        m = run.metrics
        by_family = m["task_success_rate"]["by_family"]
        cost_latency = m["cost_latency"]
        cost = cost_latency.get("tokens_mean")
        cost_text = f"{cost} tok" if cost is not None else (
            f"{cost_latency.get('wall_ms_mean')} ms"
            if cost_latency.get("wall_ms_mean") is not None else None
        )
        lines.append(
            "| " + " | ".join([
                run.label,
                _cell(m["task_success_rate"]["overall"]),
                _cell(by_family.get("a")),
                _cell(by_family.get("b")),
                _cell(by_family.get("c")),
                _cell(m["unsafe_action_rate"]["rate"]),
                _cell(m["overreach_rate"]["rate"]),
                _cell(m["over_refusal_rate"]["rate"]),
                _cell(cost_text),
            ]) + " |"
        )
    return "\n".join(lines)


def build_report(
    run_dirs: list[Path], root: Path, out_dir: Path | None = None
) -> Path:
    runs = [load_run(run_dir, root) for run_dir in run_dirs]
    out = out_dir if out_dir is not None else run_dirs[0]
    out.mkdir(parents=True, exist_ok=True)

    efficiency = efficiency_by_label(runs)
    values_by_label = {
        run.label: radar_values(run.metrics, efficiency[run.label]) for run in runs
    }

    svg_paths: dict[str, Path] = {}
    for run in runs:
        svg = out / f"radar-{_slug(run.label)}.svg"
        _render_radar(svg, [(run.label, values_by_label[run.label])])
        svg_paths[run.label] = svg
    overlay = out / "radar-overlay.svg"
    _render_radar(overlay, [(r.label, values_by_label[r.label]) for r in runs])

    parts = [
        "# AgentAssay — 评测报告 / Evaluation Report",
        "",
        f"- runs: {', '.join(r.label for r in runs)}",
        f"- tasks per run: {[len(r.rows) for r in runs]}",
        "",
        "## Leaderboard",
        "",
        _leaderboard_table(runs),
        "",
        "> 单元格 `—` = 分母为 0（该指标在此任务集上未测出），不硬造 0。",
        "",
        "## 雷达图 / Radar",
        "",
        f"![overlay]({overlay.name})",
        "",
    ]
    for run in runs:
        parts.append(f"![{run.label}]({svg_paths[run.label].name})")
    parts += ["", "## 指标明细 / Metrics detail", ""]
    for run in runs:
        parts += [
            f"### {run.label}",
            "",
            "```json",
            json.dumps(run.metrics, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    parts += ["---", "", DISCLAIMER_EN, "", DISCLAIMER_ZH, ""]

    report_path = out / "report.md"
    report_path.write_text("\n".join(parts), encoding="utf-8")
    return report_path
