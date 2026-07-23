"""FP04：工具注册表——12 工具签名、双层校验、不可逆元数据、D3。"""

from decimal import Decimal
from pathlib import Path

from open_harness.env.mock import MockExchangeEnv
from open_harness.tasks.loader import load_fixture
from open_harness.tools import registry
from open_harness.tools.registry import ToolContext, execute_tool

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_ctx(fixture="std_account_1.yaml"):
    env = MockExchangeEnv(load_fixture(REPO_ROOT / "fixtures" / fixture))
    return ToolContext(
        env=env,
        ask_user=lambda q: "用户无回应",
        request_confirmation=lambda s: "approved",
    )


# ------------------------------------------------------------- AC-04a ----

EXPECTED_TOOLS = {
    "get_balances",
    "get_ticker",
    "get_open_orders",
    "get_my_trades",
    "get_transfer_history",
    "get_trading_rules",
    "place_order",
    "cancel_order",
    "withdraw",
    "ask_user",
    "request_confirmation",
    "report",
}


def test_twelve_tools_signatures():
    tools = {t.name: t for t in registry.all_tools()}
    assert set(tools) == EXPECTED_TOOLS and len(registry.all_tools()) == 12

    def props(name):
        return tools[name].json_schema()["properties"]

    assert set(props("place_order")) == {
        "symbol", "side", "type", "qty", "quote_qty", "price", "stop_price",
    }
    assert set(props("withdraw")) == {"asset", "amount", "address", "network"}
    assert set(props("cancel_order")) == {"symbol", "order_id"}
    assert set(props("get_transfer_history")) == {"type", "start", "end"}
    assert set(props("report")) == {"text", "answer", "status"}
    # 枚举与金额字符串化（R9）进 schema
    schema = tools["place_order"].json_schema()
    assert schema["properties"]["side"]["enum"] == ["buy", "sell"]
    assert schema["properties"]["type"]["enum"] == ["market", "limit", "stop_limit"]
    qty_schema = schema["properties"]["qty"]["anyOf"]
    assert {"type": "string"}.items() <= [s for s in qty_schema if s.get("type") == "string"][0].items()
    # 类别划分：readonly 6 / trading 3 / interactive 2 / terminal 1
    by_cat = {}
    for t in registry.all_tools():
        by_cat.setdefault(t.category, []).append(t.name)
    assert len(by_cat["readonly"]) == 6
    assert set(by_cat["trading"]) == {"place_order", "cancel_order", "withdraw"}
    assert set(by_cat["interactive"]) == {"ask_user", "request_confirmation"}
    assert by_cat["terminal"] == ["report"]


# ------------------------------------------------------------- AC-04b ----


def test_schema_error_recorded():
    ctx = make_ctx()

    missing = execute_tool("place_order", {"side": "buy", "type": "market"}, ctx)
    assert not missing.ok and missing.error_kind == "schema_error"
    assert missing.error_code == "SCHEMA_VALIDATION"
    assert "symbol" in missing.error_message

    bad_enum = execute_tool(
        "place_order",
        {"symbol": "BTCUSDT", "side": "hold", "type": "market", "qty": "0.01"},
        ctx,
    )
    assert not bad_enum.ok and bad_enum.error_kind == "schema_error"

    # 幻觉参数（extra=forbid）
    extra = execute_tool("get_balances", {"account": "main"}, ctx)
    assert not extra.ok and extra.error_kind == "schema_error"

    # 金额传 float 直接 schema_error（R9）
    float_amount = execute_tool(
        "place_order",
        {"symbol": "BTCUSDT", "side": "buy", "type": "market", "qty": 0.01},
        ctx,
    )
    assert not float_amount.ok and float_amount.error_kind == "schema_error"

    unknown = execute_tool("transfer_all_funds", {}, ctx)
    assert not unknown.ok and unknown.error_code == "UNKNOWN_TOOL"

    # schema 错误不触达环境：账本不变
    assert ctx.env.get_balances()["USDT"].free == Decimal("2500")


# ------------------------------------------------------------- AC-04c ----


def test_semantic_error_exchange_codes():
    ctx = make_ctx()

    bad_symbol = execute_tool(
        "place_order",
        {"symbol": "DOGEUSDT", "side": "buy", "type": "market", "qty": "1"},
        ctx,
    )
    assert not bad_symbol.ok and bad_symbol.error_kind == "semantic_error"
    assert bad_symbol.error_code == "INVALID_SYMBOL"

    lot = execute_tool(
        "place_order",
        {"symbol": "BTCUSDT", "side": "buy", "type": "market", "qty": "0.000012"},
        ctx,
    )
    assert lot.error_code == "LOT_SIZE" and lot.error_kind == "semantic_error"
    assert "stepSize" in lot.error_message  # agent 可读、可自我修正

    ghost = execute_tool("cancel_order", {"symbol": "BTCUSDT", "order_id": "GHOST-1"}, ctx)
    assert ghost.error_code == "UNKNOWN_ORDER" and ghost.error_kind == "semantic_error"


# ------------------------------------------------------------- AC-04d ----


def test_irreversible_metadata_dynamic():
    ctx = make_ctx()

    market = execute_tool(
        "place_order",
        {"symbol": "BTCUSDT", "side": "buy", "type": "market", "qty": "0.01"},
        ctx,
    )
    assert market.ok and market.irreversible is True

    limit = execute_tool(
        "place_order",
        {"symbol": "BTCUSDT", "side": "buy", "type": "limit", "qty": "0.01", "price": "63000"},
        ctx,
    )
    assert limit.ok and limit.irreversible is False

    stop = execute_tool(
        "place_order",
        {
            "symbol": "ETHUSDT", "side": "sell", "type": "stop_limit",
            "qty": "1", "price": "3290", "stop_price": "3300",
        },
        ctx,
    )
    assert stop.ok and stop.irreversible is False

    wd = execute_tool(
        "withdraw",
        {"asset": "USDT", "amount": "10", "address": "MockDestAAA", "network": "MOCKNET"},
        ctx,
    )
    assert wd.ok and wd.irreversible is True

    ro = execute_tool("get_balances", {}, ctx)
    assert ro.ok and ro.irreversible is False

    # 语义失败的市价单：尝试仍打不可逆标（ok=False 区分未执行）
    too_big = execute_tool(
        "place_order",
        {"symbol": "BTCUSDT", "side": "buy", "type": "market", "qty": "10"},
        ctx,
    )
    assert not too_big.ok and too_big.irreversible is True


# ------------------------------------------------------------- AC-04f ----


def test_no_mandate_enforcement_in_tool_layer():
    """D3 守护：工具层无 mandate 概念，「合法格式但违反 mandate」的调用照常执行。"""
    import inspect

    # 结构保证：ToolContext 类型上就没有 mandate
    params = inspect.signature(ToolContext.__init__).parameters
    assert "mandate" not in params
    ctx = make_ctx("redteam_1.yaml")
    assert not hasattr(ctx, "mandate")

    # 超限额 + 白名单外资产：正常回执，无任何 mandate 相关错误
    over = execute_tool(
        "place_order",
        {"symbol": "MEMEUSDT", "side": "buy", "type": "market", "quote_qty": "5000"},
        ctx,
    )
    assert over.ok and over.error_code is None

    # 非白名单地址提币：照常执行
    wd = execute_tool(
        "withdraw",
        {"asset": "USDT", "amount": "50", "address": "MockEvilDestBBB", "network": "MOCKNET"},
        ctx,
    )
    assert wd.ok and wd.result["status"] == "completed"


# ---------------------------------------------------------- 交互与终止 ----


def test_interactive_and_terminal_tools():
    ctx = make_ctx()

    ask = execute_tool("ask_user", {"question": "全部卖出吗？"}, ctx)
    assert ask.ok and ask.result == {"reply": "用户无回应"}

    confirm = execute_tool("request_confirmation", {"action_summary": "市价卖出 0.5 BTC"}, ctx)
    assert confirm.ok and confirm.result == {"decision": "approved"}

    report = execute_tool(
        "report",
        {"text": "已完成", "answer": {"free_usdt": "2500"}, "status": "done"},
        ctx,
    )
    assert report.ok and report.result["status"] == "done"
    assert registry.get_tool("report").category == "terminal"
