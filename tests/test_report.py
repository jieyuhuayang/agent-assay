"""FP12 · 报告层测试（AC-12a 对账 / AC-12b 雷达 SVG + specs/12 拒收规则）。

对账口径：报表数字必须等于「按原始结果 JSON 独立重算」的期望值——测试自己做
算术（不调用 compute_metrics），报表与它相等才算 reconcile。
tags 用仓库真实任务文件（specs/12 §2：tags 来自 tasks/）。
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

from open_harness.report.build import RADAR_AXES, build_report
from open_harness.results import Fingerprint, ResultRecord, save_result
from open_harness.tasks.loader import load_task

REPO_ROOT = Path(__file__).resolve().parents[1]


def _record(task_id: str, *, passed: bool, status: str = "done", tool_calls: int = 3,
            schema_errors: int = 0, wall_ms: int = 1000) -> ResultRecord:
    return ResultRecord(
        task_id=task_id,
        status=status,
        fingerprint=Fingerprint(
            model="test-model", model_version="test-1", taskset_version="v0.1.0",
            git_commit="deadbeef", timestamp="2026-07-24T00:00:00+00:00", temperature="0",
        ),
        trajectory=[],
        final_state={"balances": {}, "open_orders": [], "new_trades": [], "new_transfers": []},
        scoring={
            "passed": passed, "assertions": [],
            "stats": {"tool_calls": tool_calls, "steps": tool_calls,
                      "schema_errors": schema_errors, "semantic_errors": 0,
                      "hallucination_calls": 0, "unsafe": False, "overreach": False,
                      "clarified": False},
            "judge": None, "judge_model": None, "judge_error": None,
        },
        timing={"wall_ms": wall_ms, "steps": [], "tokens": None},
    )


_ROWS = [
    ("A01", dict(passed=True)),
    ("A02", dict(passed=False, schema_errors=1, tool_calls=4)),
    ("A03", dict(passed=False, status="infra_error", wall_ms=500)),
    ("B01", dict(passed=True)),
    ("C01", dict(passed=True)),
]


def _write_run(run_dir: Path, label: str, rows=_ROWS) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    for task_id, kwargs in rows:
        save_result(_record(task_id, **kwargs), run_dir / f"{task_id}.json")
    (run_dir / "meta.json").write_text(
        json.dumps({"model": label}), encoding="utf-8"
    )
    return run_dir


def _q4(numerator: int, denominator: int) -> str:
    return str((Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.0001"), ROUND_HALF_EVEN))


def test_leaderboard_reconciles_with_results(tmp_path):
    """AC-12a：leaderboard 每格 == 由原始 JSON 独立重算的值。"""
    run = _write_run(tmp_path / "run1", "test-model")
    report_path = build_report([run], REPO_ROOT, out_dir=tmp_path / "out")
    text = report_path.read_text(encoding="utf-8")

    row_line = next(line for line in text.splitlines() if line.startswith("| test-model |"))
    cells = [c.strip() for c in row_line.strip("|").split("|")]
    # 独立重算：infra_error（A03）出成功率分母；A01 过 / A02 挂
    assert cells[1] == _q4(3, 4)          # overall = 3/4
    assert cells[2] == _q4(1, 2)          # a 族 = A01 过 / A02 挂（A03 infra 剔除）
    assert cells[3] == _q4(1, 1)          # b
    assert cells[4] == _q4(1, 1)          # c

    # 安全类分母来自真实任务 tags（D-c）：期望值按 tags 独立推导
    tags = {tid: load_task(REPO_ROOT / "tasks" / tid[0].lower() / f"{tid}.yaml").tags
            for tid, _ in _ROWS}
    def tag_expect(tag: str) -> str:
        opportunities = sum(1 for tid, _ in _ROWS if tag in tags[tid])
        return "—" if opportunities == 0 else _q4(0, opportunities)  # 全部行都未违规
    assert cells[5] == tag_expect("irreversible")      # Unsafe
    assert cells[6] == tag_expect("overreach_bait")    # Overreach
    assert cells[7] == tag_expect("legit_reverse")     # Over-refusal

    # 平均成本：无 tokens → wall 口径；(1000×4+500)/5 = 900.0
    assert cells[8] == "900.0 ms"

    # 免责声明必须在（AC-12c 的报表侧）
    assert "not investment advice" in text and "非投资建议" in text


def test_radar_six_axes_svg(tmp_path):
    """AC-12b：每模型一张 + 叠加一张，SVG 文本含全部六个轴标签。"""
    run1 = _write_run(tmp_path / "run1", "model-one")
    run2 = _write_run(tmp_path / "run2", "model-two")
    out = tmp_path / "out"
    build_report([run1, run2], REPO_ROOT, out_dir=out)

    svgs = sorted(p.name for p in out.glob("*.svg"))
    assert svgs == ["radar-model-one.svg", "radar-model-two.svg", "radar-overlay.svg"]
    for svg in out.glob("*.svg"):
        content = svg.read_text(encoding="utf-8")
        for axis in RADAR_AXES:
            assert axis in content, f"{svg.name} 缺轴标签 {axis}"


def test_structural_results_rejected(tmp_path):
    """specs/12 §2：testnet 结构评分结果混入报告必须 fail loud（D1）。"""
    run = tmp_path / "run-testnet"
    run.mkdir()
    record = _record("A01", passed=True)
    record.scoring = {**record.scoring, "mode": "structural"}
    save_result(record, run / "A01.json")
    with pytest.raises(ValueError, match="structural|结构"):
        build_report([run], REPO_ROOT, out_dir=tmp_path / "out")
