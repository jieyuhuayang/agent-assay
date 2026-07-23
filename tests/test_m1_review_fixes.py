"""M1 对抗审查修复的回归守卫（specs/00 · 审查记录 C1–C11）。"""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_harness.agent.providers import (
    LiteLLMProvider,
    ModelResponse,
    Provider,
    ProviderError,
    ToolCallRequest,
)
from open_harness.env.base import ExchangeError
from open_harness.env.mock import MockExchangeEnv
from open_harness.tasks.loader import load_fixture
from open_harness.tasks.validate import scan_r6
from open_harness.tools.registry import ToolContext, execute_tool

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_env(name="std_account_1.yaml"):
    return MockExchangeEnv(load_fixture(REPO_ROOT / "fixtures" / name))


def make_ctx(env):
    return ToolContext(env=env, ask_user=lambda q: "无", request_confirmation=lambda s: "approved")


# C1：市价卖单 quote_qty 不得崩溃——双向支持（Binance quoteOrderQty 语义）


def test_market_sell_quote_qty_supported():
    env = make_env()
    receipt = env.place_order("ETHUSDT", "sell", "market", quote_qty=Decimal("345"))
    # 345 / 3450 = 0.1 ETH，向下对齐 step 0.0001
    assert receipt.executed_qty == Decimal("0.1")
    assert receipt.status == "filled"

    # 经 registry 全链路：结构化结果而非异常
    inv = execute_tool(
        "place_order",
        {"symbol": "ETHUSDT", "side": "sell", "type": "market", "quote_qty": "345"},
        make_ctx(make_env()),
    )
    assert inv.ok and inv.result["executed_qty"] == "0.1"


def test_execute_tool_never_raises_on_handler_bug():
    """C1 兜底：非 ExchangeError 的处理器异常转结构化 INTERNAL_ERROR，不炸 episode。"""
    env = make_env()
    ctx = make_ctx(env)
    env.get_ticker = lambda symbol: (_ for _ in ()).throw(RuntimeError("boom"))  # 注入故障
    inv = execute_tool("get_ticker", {"symbol": "BTCUSDT"}, ctx)
    assert not inv.ok
    assert inv.error_code == "INTERNAL_ERROR"
    assert inv.error_kind is None  # 非模型过错，不计入 schema/semantic 指标


# C2：失败订单不得消耗部分成交脚本（A09 公平性）


def test_partial_fill_rule_survives_failed_order():
    env = make_env("partial_fill_1.yaml")
    with pytest.raises(ExchangeError) as exc_info:
        env.place_order("ETHUSDT", "sell", "market", qty=Decimal("20"))  # free 仅 8
    assert exc_info.value.code == "INSUFFICIENT_BALANCE"

    receipt = env.place_order("ETHUSDT", "sell", "market", qty=Decimal("2"))
    assert receipt.status == "partially_filled"
    assert receipt.executed_qty == Decimal("1.2")  # 脚本仍在


# C3：无行情快照的 symbol，三种下单同报 INVALID_SYMBOL


def test_missing_ticker_consistent_error_code():
    fixture = load_fixture(REPO_ROOT / "fixtures" / "std_account_1.yaml")
    del fixture.tickers["BNBUSDT"]
    env = MockExchangeEnv(fixture)
    cases = [
        lambda: env.place_order("BNBUSDT", "buy", "market", qty=Decimal("1")),
        lambda: env.place_order("BNBUSDT", "buy", "market", quote_qty=Decimal("100")),
        lambda: env.place_order("BNBUSDT", "buy", "limit", qty=Decimal("1"), price=Decimal("500")),
    ]
    for action in cases:
        with pytest.raises(ExchangeError) as exc_info:
            action()
        assert exc_info.value.code == "INVALID_SYMBOL"


# C4：时间窗写法宽容性——Z / +00:00 / 日期简写一致


def test_time_window_format_equivalence():
    env = make_env("rich_history.yaml")
    z = env.get_my_trades(end="2026-07-16T14:00:00Z")
    offset = env.get_my_trades(end="2026-07-16T14:00:00+00:00")
    assert [t.trade_id for t in z] == [t.trade_id for t in offset]
    assert any(t.trade_id == "H-1008" for t in z)  # 边界时刻记录被包含

    date_only = env.get_my_trades(start="2026-07-10", end="2026-07-10")
    assert [t.trade_id for t in date_only] == ["H-1001", "H-1002", "H-1003"]

    with pytest.raises(ExchangeError):
        env.get_my_trades(start="not-a-time")


# C5/C8：litellm 响应解析边界


def _fake_litellm_provider(fake_response):
    provider = LiteLLMProvider.__new__(LiteLLMProvider)
    provider.model_name = "fake"
    provider.model_version = "fake"
    provider._timeout = 5
    provider._litellm = SimpleNamespace(completion=lambda **kw: fake_response)
    return provider


def _resp(tool_calls=None, content=None, choices=...):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    if choices is ...:
        choices = [SimpleNamespace(message=message)]
    return SimpleNamespace(model="fake-v1", choices=choices)


def test_non_dict_tool_arguments_degrade_to_raw():
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="report", arguments="[]"))
    response = _fake_litellm_provider(_resp(tool_calls=[call])).complete([], [])
    assert response.tool_calls[0].arguments is None
    assert response.tool_calls[0].arguments_raw == "[]"


def test_malformed_response_raises_provider_error():
    with pytest.raises(ProviderError):
        _fake_litellm_provider(_resp(choices=[])).complete([], [])


# C6/C7：runner 消息协议


def test_assistant_message_protocol():
    from open_harness.agent.runner import _assistant_message

    # 空响应：content 归一为字符串
    empty = _assistant_message(ModelResponse(text=None, tool_calls=[]))
    assert empty["content"] == ""

    # 非法参数原文包成合法 JSON object，不双重编码裸串
    raw = _assistant_message(
        ModelResponse(
            tool_calls=[ToolCallRequest(id="c1", name="place_order", arguments_raw="{bad")]
        )
    )
    arguments = json.loads(raw["tool_calls"][0]["function"]["arguments"])
    assert arguments == {"_raw": "{bad"}


# C9：R6 扫描的 CJK 紧邻场景


def test_r6_scan_cjk_adjacency():
    eth = "0x" + "a1b2c3d4e5" * 4
    tron = "T" + "9yD2PjKwzV8rHcGeQaUuFmXsRt5vNqBhJ"
    privkey = "e9873d79c6d87dc0" * 4
    assert scan_r6(f"把USDT提到{eth}就行") == ["eth_address"]
    assert "tron_address" in scan_r6(f"新地址{tron}马上转")
    assert "hex_private_key" in scan_r6(f"私钥是{privkey}别告诉人")
    assert scan_r6("提到白名单地址 TMockWhitelistedAddressAAA") == []


# C10：R2 无引号赋值形态的自检（样本动态拼接，避免扫描自伤）


def test_r2_unquoted_secret_pattern_selfcheck():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "test_redlines_mod", Path(__file__).parent / "test_redlines.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sample = "api" + "_key = " + "Ab1" * 12
    assert any(p.search(sample) for p in mod._SECRET_PATTERNS)
    sample2 = "API" + "_SECRET: " + "Zx9" * 12
    assert any(p.search(sample2) for p in mod._SECRET_PATTERNS)
