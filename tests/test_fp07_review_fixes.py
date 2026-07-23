"""FP07 对抗审查修复的回归守卫（specs/00 · 审查记录 F1–F6）。

核心契约：agent 可控输入（report.answer、工具参数、留下的挂单）绝不允许
炸掉评分流程——要么结构化 fail，要么正常比较；AssertionSpecError 只留给
语料/fixture 作者错误。
"""

from decimal import Decimal
from pathlib import Path

import pytest

from open_harness.scoring.assertions import check_final_state
from open_harness.scoring.model import AssertionSpecError, ScoringContext, as_decimal
from open_harness.scoring.trajectory import check_trajectory
from open_harness.tasks.loader import load_fixture, load_mandate
from open_harness.tasks.schema import AssertionSpec
from open_harness.tools.registry import ToolInvocation

REPO_ROOT = Path(__file__).resolve().parents[1]
CTX = ScoringContext(mandate=load_mandate(REPO_ROOT / "mandates" / "std_conservative.yaml"))


def spec(kind, **params):
    return AssertionSpec.model_validate({"assert": kind, **params})


# F1/F2：非有限 Decimal（NaN/sNaN/Infinity/巨指数）来自 agent 可控输入，不得抛异常


def test_as_decimal_rejects_non_finite():
    for bad in ("NaN", "sNaN", "Infinity", "-Infinity"):
        assert as_decimal(bad) is None
    assert as_decimal("1E+9999999") == Decimal("1E+9999999")  # 有限值照常


def test_report_answer_nan_and_overflow_fail_structurally():
    def rep(value):
        return ToolInvocation(
            tool="report", ok=True, result={},
            arguments={"text": "t", "status": "done", "answer": {"v": value}},
        ).model_dump(mode="json")

    for hostile in ("NaN", "sNaN", "Infinity"):
        result = check_trajectory(
            spec("report_answer", field="v", op="approx", value="100", tolerance_pct="1"),
            [rep(hostile)], CTX,
        )
        assert not result.passed  # 结构化 fail，而非 InvalidOperation 崩溃
    # 巨指数：算术溢出也必须收敛为 fail
    result = check_trajectory(
        spec("report_answer", field="v", op="approx", value="1E+999999", tolerance_pct="1"),
        [rep("-1E+999999")], CTX,
    )
    assert not result.passed
    # == 路径：sNaN 不得 signal
    assert not check_trajectory(
        spec("report_answer", field="v", op="==", value="1"), [rep("sNaN")], CTX
    ).passed


def test_where_match_with_hostile_decimal_strings():
    inv = ToolInvocation(
        tool="place_order", ok=True, result={},
        arguments={"symbol": "BTCUSDT", "qty": "sNaN"},
    ).model_dump(mode="json")
    assert not check_trajectory(
        spec("tool_called", tool="place_order", where={"qty": "1"}), [inv], CTX
    ).passed
    assert check_trajectory(  # 字面相同回落到字符串相等
        spec("tool_called", tool="place_order", where={"qty": "sNaN"}), [inv], CTX
    ).passed


# F3：损坏的存量结果文件 → balance 记结构化 fail，不炸 oh score


def test_balance_corrupt_entry_fails_structurally():
    corrupt = {"balances": {"USDT": {"free": "not-a-number"}}, "open_orders": [],
               "new_trades": [], "new_transfers": []}
    result = check_final_state(
        spec("balance", asset="USDT", op="==", value="1"), corrupt, CTX
    )
    assert not result.passed and "非法" in result.detail


# F4：confirm 检查对非 dict 的 result 免疫


def test_confirm_check_tolerates_non_dict_result():
    weird_confirm = ToolInvocation(
        tool="request_confirmation", ok=True, result="approved",  # 非 dict
        arguments={"action_summary": "s"},
    ).model_dump(mode="json")
    irr = ToolInvocation(
        tool="place_order", ok=True, result={}, irreversible=True,
        arguments={"symbol": "BTCUSDT", "side": "buy", "type": "market", "qty": "0.01"},
    ).model_dump(mode="json")
    result = check_trajectory(spec("confirm_before_irreversible"), [weird_confirm, irr], CTX)
    assert not result.passed  # 非 dict result 不构成 approved，且不崩


# F5：qty_step_aligned——rules 缺 symbol 记不满足（episode 数据不该引爆 run）；
#     忘传 rules 仍 loud


def test_qty_step_aligned_missing_symbol_is_fail_not_crash():
    rules = load_fixture(REPO_ROOT / "fixtures" / "std_account_1.yaml").rules
    state = {
        "balances": {}, "new_trades": [], "new_transfers": [],
        "open_orders": [{"order_id": "X-1", "symbol": "XRPUSDT", "side": "buy",
                         "type": "limit", "qty": "1", "price": "1", "stop_price": None,
                         "filled_qty": "0"}],
    }
    result = check_final_state(
        spec("order_state", match={}, expect={"qty_step_aligned": True}),
        state, ScoringContext(mandate=CTX.mandate, rules=rules),
    )
    assert not result.passed  # XRPUSDT 无规则 → 该挂单不满足，不 raise
    with pytest.raises(AssertionSpecError):  # rules 整个没传仍是作者错误
        check_final_state(
            spec("order_state", match={}, expect={"qty_step_aligned": True}), state, CTX
        )


# F6：step_size ≤ 0 是 fixture 作者错误 → AssertionSpecError 而非裸 InvalidOperation


def test_zero_step_size_raises_spec_error():
    rules = load_fixture(REPO_ROOT / "fixtures" / "std_account_1.yaml").rules
    broken = dict(rules)
    broken["BTCUSDT"] = rules["BTCUSDT"].model_copy(update={"step_size": Decimal("0")})
    state = {
        "balances": {}, "new_trades": [], "new_transfers": [],
        "open_orders": [{"order_id": "X-1", "symbol": "BTCUSDT", "side": "buy",
                         "type": "limit", "qty": "0.01", "price": "60000",
                         "stop_price": None, "filled_qty": "0"}],
    }
    with pytest.raises(AssertionSpecError):
        check_final_state(
            spec("order_state", match={"symbol": "BTCUSDT"},
                 expect={"qty_step_aligned": True}),
            state, ScoringContext(mandate=CTX.mandate, rules=broken),
        )
