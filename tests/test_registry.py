"""FP04：工具注册表——12 工具签名、双层校验、不可逆元数据、D3。"""

from decimal import Decimal
from pathlib import Path

from agent_assay.env.mock import MockExchangeEnv
from agent_assay.tasks.loader import load_fixture
from agent_assay.tools import registry
from agent_assay.tools.registry import ToolContext, execute_tool

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


# ================= FP14 · registry profile 与 x402 工具（AC-14a–e，specs/14）=====


def test_x402_tools_signatures():
    """AC-14a：x402 profile = 4 新工具 + 3 共享，顺序与签名固定。"""
    from agent_assay.tools.registry import all_tools

    x402 = {t.name: t for t in all_tools(profile="x402")}
    assert list(x402) == [
        "get_wallet", "get_payment_history", "http_fetch", "x402_pay",
        "ask_user", "request_confirmation", "report",
    ]
    assert x402["get_wallet"].category == "readonly"
    assert x402["http_fetch"].category == "readonly"
    assert x402["x402_pay"].category == "trading"
    pay_schema = x402["x402_pay"].json_schema()
    assert set(pay_schema["properties"]) == {"url", "amount", "pay_to"}
    assert set(pay_schema.get("required", [])) == {"url", "amount", "pay_to"}
    fetch_schema = x402["http_fetch"].json_schema()
    assert set(fetch_schema["properties"]) == {"url"}


def test_all_tools_default_exchange_unchanged():
    """AC-14b（R4 承重）：缺省 profile 仍返回原 12 工具原顺序。"""
    from agent_assay.tools.registry import all_tools

    assert [t.name for t in all_tools()] == [
        "get_balances", "get_ticker", "get_open_orders", "get_my_trades",
        "get_transfer_history", "get_trading_rules", "place_order",
        "cancel_order", "withdraw", "ask_user", "request_confirmation", "report",
    ]


def test_profile_filtering():
    """AC-14c：exchange=12、x402=7（含共享 3）、全集 16。"""
    from agent_assay.tools.registry import TOOL_DEFS, all_tools

    assert len(all_tools("exchange")) == 12
    assert len(all_tools("x402")) == 7
    assert len(TOOL_DEFS) == 16
    shared = {t.name for t in all_tools("exchange")} & {t.name for t in all_tools("x402")}
    assert shared == {"ask_user", "request_confirmation", "report"}


def test_x402_pay_irreversible_metadata():
    """AC-14d：x402_pay 恒不可逆（R8 元数据）、category=trading（clarify 复用，D-q）。"""
    from agent_assay.tools.registry import get_tool

    tool = get_tool("x402_pay")
    params = tool.params_model.model_validate(
        {"url": "https://a.example/r", "amount": "5", "pay_to": "PAYMOCKMerchantAAA"}
    )
    assert tool.irreversible_fn(params) is True
    assert tool.category == "trading"


def _x402_ctx(wallet_free: str = "500"):
    import yaml as _yaml

    from agent_assay.env.x402 import X402MockEnv
    from agent_assay.env.x402_fixture import X402FixtureSpec
    from agent_assay.tools.registry import ToolContext

    fixture = X402FixtureSpec.model_validate(_yaml.safe_load(f"""
kind: x402
wallet:
  USDC: {{free: "{wallet_free}", locked: "0"}}
resources:
  - url: https://reports.example/brief
    price: "5"
    pay_to: PAYMOCKMerchantAAA
    content: fine
"""))
    return ToolContext(
        env=X402MockEnv(fixture),
        ask_user=lambda q: "ok",
        request_confirmation=lambda s: "approved",
    )


def test_no_mandate_enforcement_x402_tools():
    """AC-14e（D3）：越限金额 / 非白名单 payee 经 registry 照常执行出正常回执。"""
    from agent_assay.tools.registry import execute_tool

    ctx = _x402_ctx()
    over = execute_tool(
        "x402_pay",
        {"url": "https://reports.example/brief", "amount": "180", "pay_to": "PAYMOCKEvilZZZ"},
        ctx,
    )
    assert over.ok is True
    assert over.result["settled"] is False and over.result["amount"] == "180"
    assert over.irreversible is True  # R8 快照

    fetch = execute_tool("http_fetch", {"url": "https://reports.example/brief"}, ctx)
    assert fetch.ok and fetch.result["status"] == 402

    ghost = execute_tool("http_fetch", {"url": "https://nowhere.example/x"}, ctx)
    assert not ghost.ok and ghost.error_code == "UNKNOWN_RESOURCE"
    assert ghost.error_kind == "semantic_error"  # EnvError 走既有捕获路径
