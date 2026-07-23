"""AC-09a：B 族 10 条全过 validate、每条含 report_answer、只读不动仓、D-d 点差约束。"""

from decimal import Decimal
from pathlib import Path

from open_harness.tasks.loader import load_fixture, load_task
from open_harness.tasks.validate import validate_repo

REPO_ROOT = Path(__file__).resolve().parents[1]
B_IDS = [f"B{i:02d}" for i in range(1, 11)]


def load_b_tasks():
    return {tid: load_task(REPO_ROOT / "tasks" / "b" / f"{tid}.yaml") for tid in B_IDS}


def test_b_family_validate_and_report_answer():
    report = validate_repo(REPO_ROOT)
    assert report.ok, [f"{i.code}: {i.file} {i.message}" for i in report.issues]

    tasks = load_b_tasks()
    assert set(tasks) == set(B_IDS)
    for tid, task in tasks.items():
        assert task.family == "b", tid
        kinds = [a.kind for a in task.expected.trajectory]
        assert "report_answer" in kinds, f"{tid} 必须以 report.answer 结构化判分"
        # 只读任务不许动仓：终态一律钉 no_new_trades
        assert "no_new_trades" in [a.kind for a in task.expected.final_state], tid
        # 防拍脑袋答对：必须调用其信息来源工具
        assert "tool_called" in kinds, tid
        # 题面必须显式给出 answer 字段名（不考猜 schema）
        for spec in task.expected.trajectory:
            if spec.kind == "report_answer":
                field = spec.model_dump()["field"]
                assert field in task.instruction, f"{tid} 题面未给出字段名 {field}"


def test_b_valuation_spread_within_tolerance():
    """D-d（AC-09a 附带）：估值类任务（B02/B06）涉及 symbol 的点差 ≤ 0.1%。"""
    for tid in ("B02", "B06"):
        task = load_task(REPO_ROOT / "tasks" / "b" / f"{tid}.yaml")
        fixture = load_fixture(REPO_ROOT / task.fixture)
        for symbol, ticker in fixture.tickers.items():
            spread_pct = (ticker.ask - ticker.bid) / ticker.last * Decimal("100")
            assert spread_pct <= Decimal("0.1"), f"{tid}/{symbol} 点差 {spread_pct}% 超 D-d 约束"
