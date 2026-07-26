"""ExchangeEnv 接口（FP03）：mock 与 testnet 的共同契约（D1）。

语义错误以 ExchangeError(code, message) 抛出，FP04 registry 捕获后转为工具错误
并记 semantic_error。错误码采用交易所风格：
INVALID_SYMBOL / INVALID_ORDER / LOT_SIZE / PRICE_FILTER / MIN_NOTIONAL /
INSUFFICIENT_BALANCE / UNKNOWN_ORDER。

D3 边界：环境不做任何 mandate 维度检查（限额/资产白名单/提币地址/确认策略）——
越界动作必须可真实执行，越界与否由评分侧判定；唯一硬约束是 R1（不碰主网）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ..money import Money
from .fixture import AssetBalance, OpenOrderFx, SymbolRulesFx, TickerFx, TradeFx, TransferFx


class ExchangeError(Exception):
    """语义层错误（交易所风格错误码）。schema 层错误由 FP04 registry 处理。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# 非交易所环境（x402 等）的语义错误同走此类型——registry 的捕获路径零改动（specs/13 D-p）
EnvError = ExchangeError


class InvariantViolation(RuntimeError):
    """环境自身一致性被破坏（余额/冻结/挂单不守恒）——测试环境的正确性护栏。"""


class BaseEnv(ABC):
    """一切评测环境的最小契约：终态快照。

    工具层按 profile 鸭子类型调用各域的具体方法（D2 姿态不变）；runner 只依赖本契约。
    """

    @abstractmethod
    def export_state(self) -> dict[str, Any]:
        """终态快照（含增量字段），供结果落盘与断言。"""


class Fill(BaseModel):
    price: Money
    qty: Money
    fee: Money
    fee_asset: str


class OrderReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop_limit"]
    status: Literal["new", "filled", "partially_filled", "canceled"]
    executed_qty: Money
    cum_quote: Money  # 成交的 quote 总额（不含费；spend_within 口径素材）
    fills: list[Fill] = []
    requested_qty: Money | None = None
    quote_qty: Money | None = None
    price: Money | None = None
    stop_price: Money | None = None
    created_at: str = ""


class WithdrawReceipt(BaseModel):
    transfer_id: str
    asset: str
    amount: Money
    address: str
    network: str
    status: str = "completed"
    simulated: bool = False  # testnet 模式为 True（第 5 节；FP11）
    timestamp: str = ""


class ExchangeEnv(BaseEnv):
    """交易所环境接口。工具注册表（FP04）是唯一调用方。"""

    # ---- 只读 ----

    @abstractmethod
    def get_balances(self) -> dict[str, AssetBalance]: ...

    @abstractmethod
    def get_ticker(self, symbol: str) -> TickerFx: ...

    @abstractmethod
    def get_trading_rules(self, symbol: str) -> SymbolRulesFx: ...

    @abstractmethod
    def get_open_orders(self, symbol: str | None = None) -> list[OpenOrderFx]: ...

    @abstractmethod
    def get_my_trades(
        self, symbol: str | None = None, start: str | None = None, end: str | None = None
    ) -> list[TradeFx]: ...

    @abstractmethod
    def get_transfer_history(
        self, type: str, start: str | None = None, end: str | None = None
    ) -> list[TransferFx]: ...

    # ---- 可写 ----

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        type: Literal["market", "limit", "stop_limit"],
        qty: Money | None = None,
        quote_qty: Money | None = None,
        price: Money | None = None,
        stop_price: Money | None = None,
    ) -> OrderReceipt: ...

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> OrderReceipt: ...

    @abstractmethod
    def withdraw(
        self, asset: str, amount: Money, address: str, network: str
    ) -> WithdrawReceipt: ...

    # ---- 快照 ----

    @abstractmethod
    def export_state(self) -> dict[str, Any]:
        """终态快照（含 new_trades / new_transfers 增量），供结果落盘与断言。"""
