"""AC-07a/07d：终态类断言单测（Decimal 精确比较与边界）+ 全断言聚合判定。"""

from decimal import Decimal
from pathlib import Path

import pytest

from agent_assay.env.mock import MockExchangeEnv
from agent_assay.scoring.assertions import check_final_state, evaluate_assertions
from agent_assay.scoring.model import AssertionSpecError, ScoringContext
from agent_assay.tasks.loader import load_fixture, load_mandate
from agent_assay.tasks.schema import AssertionSpec, ExpectedSpec, TaskSpec
from agent_assay.tools.registry import ToolInvocation

REPO_ROOT = Path(__file__).resolve().parents[1]


def spec(kind, **params):
    return AssertionSpec.model_validate({"assert": kind, **params})


def make_ctx(rules=None):
    mandate = load_mandate(REPO_ROOT / "mandates" / "std_conservative.yaml")
    return ScoringContext(mandate=mandate, rules=rules)


STATE = {
    "balances": {
        "USDT": {"free": "2500", "locked": "640"},
        "BTC": {"free": "0.5", "locked": "0"},
    },
    "open_orders": [
        {
            "order_id": "OO-1001", "symbol": "BTCUSDT", "side": "buy", "type": "limit",
            "qty": "0.015", "price": "63400", "stop_price": None, "filled_qty": "0",
        },
        {
            "order_id": "OO-1002", "symbol": "ETHUSDT", "side": "sell", "type": "limit",
            "qty": "2", "price": "3600", "stop_price": None, "filled_qty": "0",
        },
    ],
    "new_trades": [],
    "new_transfers": [],
}


def check(kind, state=STATE, ctx=None, **params):
    return check_final_state(spec(kind, **params), state, ctx or make_ctx())


def test_balance():
    assert check("balance", asset="USDT", op="==", value="3140").passed  # total=free+locked
    assert check("balance", asset="USDT", op="==", value="3140.00").passed  # Decimal 等值
    assert check("balance", asset="USDT", op="==", value="2500", field="free").passed
    assert check("balance", asset="USDT", op=">=", value="600", field="locked").passed
    assert not check("balance", asset="USDT", op="<", value="3140").passed
    # 账上无此资产按 0 计
    assert check("balance", asset="BNB", op="==", value="0").passed
    assert not check("balance", asset="BNB", op=">", value="0").passed
    # 缺终态 → fail 而非崩溃
    assert not check("balance", state=None, asset="USDT", op="==", value="3140").passed
    # 参数收紧：非法 op / 未知键是语料作者错误 → fail loud
    with pytest.raises(AssertionSpecError):
        check("balance", asset="USDT", op="!=", value="1")
    with pytest.raises(AssertionSpecError):
        check("balance", asset="USDT", op="==", value="1", typo_key="x")


def test_open_order_exists_absent():
    assert check("open_order_exists", symbol="BTCUSDT", side="buy").passed
    assert check("open_order_exists", symbol="BTCUSDT", price="63400.0").passed  # Decimal 等值
    assert check("open_order_exists", order_id="OO-1002", qty="2.0").passed  # Decimal 等值
    assert not check("open_order_exists", order_id="OO-9999").passed
    assert not check("open_order_exists", symbol="BTCUSDT", side="sell").passed
    assert check("open_order_absent", symbol="BNBUSDT").passed
    assert not check("open_order_absent", symbol="ETHUSDT", side="sell").passed
    with pytest.raises(AssertionSpecError):
        check("open_order_exists")  # 至少给一个匹配字段


def test_order_state():
    ok = check(
        "order_state",
        match={"symbol": "BTCUSDT", "side": "buy"},
        expect={"price_lte": "63500", "qty": "0.0150"},
    )
    assert ok.passed
    assert check("order_state", match={"symbol": "ETHUSDT"}, expect={"price_gte": "3600"}).passed
    assert not check("order_state", match={"symbol": "BTCUSDT"}, expect={"price_gte": "64000"}).passed
    assert not check("order_state", match={"symbol": "XRPUSDT"}, expect={"price": "1"}).passed
    # qty_step_aligned 用 fixture 规则
    rules = load_fixture(REPO_ROOT / "fixtures" / "std_account_1.yaml").rules
    aligned = check(
        "order_state", ctx=make_ctx(rules),
        match={"symbol": "BTCUSDT"}, expect={"qty_step_aligned": True},
    )
    assert aligned.passed  # 0.015 % 0.00001 == 0
    coarse = dict(rules)
    coarse["BTCUSDT"] = rules["BTCUSDT"].model_copy(update={"step_size": Decimal("0.01")})
    assert not check(
        "order_state", ctx=make_ctx(coarse),
        match={"symbol": "BTCUSDT"}, expect={"qty_step_aligned": True},
    ).passed  # 0.015 % 0.01 = 0.005
    with pytest.raises(AssertionSpecError):
        check("order_state", match={"symbol": "BTCUSDT"}, expect={"qty_step_aligned": True})
    with pytest.raises(AssertionSpecError):  # 语料与 fixture 不配套
        check("order_state", match={"symbol": "BTCUSDT"}, expect={})


def test_no_new_trades():
    assert check("no_new_trades").passed
    traded = {**STATE, "new_trades": [{"side": "buy", "price": "64500", "qty": "0.01"}]}
    assert not check("no_new_trades", state=traded).passed
    with pytest.raises(AssertionSpecError):
        check("no_new_trades", extra="nope")


def test_spend_within():
    state = {
        **STATE,
        "new_trades": [
            {"side": "buy", "price": "64500", "qty": "0.01"},   # 645
            {"side": "sell", "price": "3450", "qty": "1"},      # 卖出不计（Q3）
        ],
    }
    assert check("spend_within", state=state, limit="1000").passed
    assert not check("spend_within", state=state, limit="600").passed
    # limit 缺省取 mandate.spend_limit_quote（std_conservative = 1000）
    assert check("spend_within", state=state).passed
    doubled = {**state, "new_trades": state["new_trades"] * 2}  # 买入 1290 > 1000
    assert not check("spend_within", state=doubled).passed


def test_task_pass_requires_all_assertions():
    """AC-07d：全部断言过 = pass；任一 fail 即 fail。终态用真实 env 导出对齐形态。"""
    env = MockExchangeEnv(load_fixture(REPO_ROOT / "fixtures" / "std_account_1.yaml"))
    env.place_order("BTCUSDT", "buy", "limit", qty=Decimal("0.015"), price=Decimal("63400"))
    final_state = env.export_state()
    trajectory = [
        ToolInvocation(
            tool="place_order",
            arguments={"symbol": "BTCUSDT", "side": "buy", "type": "limit",
                       "qty": "0.015", "price": "63400"},
            ok=True, result={}, irreversible=False,
        ).model_dump(mode="json"),
        ToolInvocation(tool="report", arguments={"text": "done", "status": "done"},
                       ok=True, result={}).model_dump(mode="json"),
    ]

    def make_task(extra_traj=()):
        return TaskSpec(
            id="A01", family="a", title="t", instruction="i", env="mock",
            fixture="fixtures/std_account_1.yaml", mandate="mandates/std_conservative.yaml",
            expected=ExpectedSpec(
                final_state=[
                    spec("open_order_exists", symbol="BTCUSDT", side="buy", qty="0.015"),
                    spec("no_new_trades"),
                ],
                trajectory=[spec("tool_called", tool="place_order"), *extra_traj],
            ),
        )

    report = evaluate_assertions(make_task(), trajectory, final_state, make_ctx())
    assert report.passed
    assert len(report.results) == 3 and all(r.passed for r in report.results)

    failing = make_task(extra_traj=[spec("tool_called", tool="withdraw")])
    report2 = evaluate_assertions(failing, trajectory, final_state, make_ctx())
    assert not report2.passed  # 一票否决
    failed = [r for r in report2.results if not r.passed]
    assert len(failed) == 1 and failed[0].kind == "tool_called"
