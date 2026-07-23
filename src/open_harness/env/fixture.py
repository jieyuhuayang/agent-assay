"""Fixture schema（KICKOFF 第 10 节）：数字一律字符串 → Decimal（R9）。

mock 撮合配置（滑点 bp、部分成交脚本）也在 fixture 中声明，保证可重放（R4）。
partial_fills 的规则结构由 FP03 定型，此处先以 dict 透传。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from ..money import Money


class AssetBalance(BaseModel):
    free: Money
    locked: Money = Decimal("0")


class OpenOrderFx(BaseModel):
    order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["limit", "stop_limit"]
    qty: Money
    price: Money | None = None
    stop_price: Money | None = None
    filled_qty: Money = Decimal("0")


class TradeFx(BaseModel):
    trade_id: str
    symbol: str
    side: Literal["buy", "sell"]
    price: Money
    qty: Money
    fee: Money
    fee_asset: str
    timestamp: str  # ISO-8601 UTC
    order_id: str | None = None


class TransferFx(BaseModel):
    transfer_id: str
    type: Literal["deposit", "withdraw"]
    asset: str
    amount: Money
    timestamp: str  # ISO-8601 UTC
    address: str | None = None
    network: str | None = None
    memo: str = ""  # C08 工具结果污染注入语料的载体字段


class SymbolRulesFx(BaseModel):
    """LOT_SIZE / MIN_NOTIONAL / PRICE_FILTER / 费率（第 5 节 get_trading_rules 返回面）。

    base/quote：symbol 的资产拆分（冻结与记账依据，如 BTCUSDT → BTC/USDT）。
    """

    base: str
    quote: str
    step_size: Money
    min_qty: Money
    min_notional: Money
    tick_size: Money
    price_min: Money
    price_max: Money
    maker_fee: Money
    taker_fee: Money


class TickerFx(BaseModel):
    bid: Money
    ask: Money
    last: Money
    high_24h: Money
    low_24h: Money
    volume_24h: Money


class PartialFillRule(BaseModel):
    """部分成交脚本（仅作用于市价单，按序消耗；供 A09）。"""

    ratio: Money  # 成交比例 (0,1]
    symbol: str | None = None
    side: Literal["buy", "sell"] | None = None


class MockConfig(BaseModel):
    slippage_bp: Money = Decimal("0")
    start_time: str = "2026-07-20T00:00:00Z"  # 逻辑时钟起点（R4 确定性）
    partial_fills: list[PartialFillRule] = []


class FixtureSpec(BaseModel):
    balances: dict[str, AssetBalance]
    rules: dict[str, SymbolRulesFx]
    tickers: dict[str, TickerFx]
    open_orders: list[OpenOrderFx] = []
    trades: list[TradeFx] = []
    transfers: list[TransferFx] = []
    mock: MockConfig = MockConfig()
