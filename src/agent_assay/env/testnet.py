"""Testnet 环境（FP11，specs/11）：ccxt binance + sandbox，接 Binance Spot Testnet。

- R1（D-i）：构造时对 ccxt urls['api'] 剪枝——host 不过 net.check_url 的条目直接删除，
  client 结构上无法触达白名单外域名；
- R2（D-m）：key 仅 OH_TESTNET_API_KEY / OH_TESTNET_API_SECRET；
- D1 定位：真实性演示 / API 兼容验证；正式跑分一律 mock，本环境不做增量对账
  （export_state 的 new_trades/new_transfers 恒空，断言在结构评分模式不运行）；
- withdraw 永不发真实请求（AC-11c）；网络失败 → TestnetUnavailableError 且
  消息提示改用 mock（AC-11d）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from itertools import count
from typing import Any, Callable, Literal

import ccxt  # 全仓库唯一允许 import ccxt 的模块（redlines _CCXT_ALLOWLIST）

from ..money import Money
from ..net import ForbiddenHostError, check_url
from ..secrets import TESTNET_API_KEY_ENV, TESTNET_API_SECRET_ENV, get_secret
from .base import ExchangeEnv, ExchangeError, Fill, OrderReceipt, WithdrawReceipt
from .fixture import AssetBalance, OpenOrderFx, SymbolRulesFx, TickerFx, TradeFx, TransferFx

_MOCK_HINT = "testnet 网络不可达：请检查网络后重试，或改用 --env mock（确定性本地环境）"


class TestnetConfigError(RuntimeError):
    """配置错误（key 缺失 / ccxt 版本不兼容）——启动即失败，不静默降级。"""


class TestnetUnavailableError(RuntimeError):
    """网络不可达（AC-11d）。非 ExchangeError：registry 兜底记 INTERNAL_ERROR，
    error_kind=None，不计入模型的 schema/semantic 错误指标。"""


def _dec(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return Decimal(default)


def _map_ccxt_error(exc: Exception) -> Exception:
    """specs/11 D-j 映射表。注意顺序：NetworkError 先于 ExchangeError，
    OrderNotFound 先于其父类 InvalidOrder。"""
    if isinstance(exc, ccxt.NetworkError):
        return TestnetUnavailableError(f"{_MOCK_HINT}（{type(exc).__name__}: {exc}）")
    if isinstance(exc, ccxt.BadSymbol):
        return ExchangeError("INVALID_SYMBOL", str(exc))
    if isinstance(exc, ccxt.InsufficientFunds):
        return ExchangeError("INSUFFICIENT_BALANCE", str(exc))
    if isinstance(exc, ccxt.OrderNotFound):
        return ExchangeError("UNKNOWN_ORDER", str(exc))
    if isinstance(exc, ccxt.InvalidOrder):
        return ExchangeError("INVALID_ORDER", str(exc))
    if isinstance(exc, ccxt.ExchangeError):
        return ExchangeError("EXCHANGE_ERROR", str(exc))
    return exc


def _prune_urls(client: Any) -> None:
    """D-i：sandbox 后的 urls['api'] 只保留过 R1 白名单的条目。"""
    api = client.urls.get("api")
    if not isinstance(api, dict):
        raise TestnetConfigError("ccxt 版本不兼容：urls['api'] 非 dict，无法执行 R1 剪枝")
    kept: dict[str, str] = {}
    for name, url in api.items():
        if not isinstance(url, str):
            continue  # 非 URL 条目一律丢弃
        try:
            kept[name] = check_url(url)
        except ForbiddenHostError:
            continue
    if "public" not in kept or "private" not in kept:
        raise TestnetConfigError("ccxt 版本不兼容：sandbox spot public/private 入口缺失")
    client.urls["api"] = kept


def _build_client() -> Any:
    key = get_secret(TESTNET_API_KEY_ENV)
    secret = get_secret(TESTNET_API_SECRET_ENV)
    if not key or not secret:
        raise TestnetConfigError(
            f"testnet 模式需要环境变量 {TESTNET_API_KEY_ENV} 与 {TESTNET_API_SECRET_ENV}"
            "（key 仅此来源，R2）；可到 https://testnet.binance.vision 用 GitHub 账号免费领取"
        )
    client = ccxt.binance(
        {
            "apiKey": key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "fetchMarkets": ["spot"],
                # 与 mock 语义对齐（M3 审查修复）：
                # ① 无 symbol 的 fetch_open_orders 不许预抛（否则 export_state 恒失败）
                "warnOnFetchOpenOrdersWithoutSymbol": False,
                # ② 市价买以 qty 计基础币数量，不要求 price（mock 同款口径）
                "createMarketBuyOrderRequiresPrice": False,
            },
        }
    )
    client.set_sandbox_mode(True)
    _prune_urls(client)
    return client


def _iso_to_ms(value: str) -> int | None:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


class TestnetExchangeEnv(ExchangeEnv):
    """ExchangeEnv 的 testnet 实现。client 注入仅供测试；缺省 ccxt + 剪枝。"""

    def __init__(self, client: Any | None = None) -> None:
        self.client = client if client is not None else _build_client()
        self._symbol_by_id: dict[str, str] | None = None
        self._sim_counter = count(1)

    # ------------------------------------------------------------ 内部 ----

    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 —— D-j 统一错误映射边界
            mapped = _map_ccxt_error(exc)
            if mapped is exc:
                raise
            raise mapped from exc

    def ping(self) -> None:
        """连通性预检（CLI 起跑前调用）；失败 raise TestnetUnavailableError。"""
        self._call(self.client.fetch_time)

    def _market_symbol(self, symbol_id: str) -> str:
        """本仓库 symbol id（BTCUSDT）→ ccxt 统一符号（BTC/USDT）。markets 懒加载。"""
        if self._symbol_by_id is None:
            markets = self._call(self.client.load_markets)
            self._symbol_by_id = {m["id"]: m["symbol"] for m in markets.values() if m.get("id")}
        ccxt_symbol = self._symbol_by_id.get(symbol_id)
        if ccxt_symbol is None:
            raise ExchangeError("INVALID_SYMBOL", f"unknown symbol: {symbol_id}")
        return ccxt_symbol

    def _id_of(self, unified: str | None) -> str:
        market = (getattr(self.client, "markets", None) or {}).get(unified or "")
        if isinstance(market, dict) and market.get("id"):
            return market["id"]
        return (unified or "").replace("/", "")

    # ------------------------------------------------------------ 只读 ----

    def get_balances(self) -> dict[str, AssetBalance]:
        raw = self._call(self.client.fetch_balance)
        out: dict[str, AssetBalance] = {}
        for asset, free in (raw.get("free") or {}).items():
            locked = (raw.get("used") or {}).get(asset)
            free_d, locked_d = _dec(free), _dec(locked)
            if free_d == 0 and locked_d == 0:
                continue  # testnet 账户资产条目上百，全零的不进账面
            out[asset] = AssetBalance(free=free_d, locked=locked_d)
        return out

    def get_ticker(self, symbol: str) -> TickerFx:
        t = self._call(self.client.fetch_ticker, self._market_symbol(symbol))
        return TickerFx(
            bid=_dec(t.get("bid")),
            ask=_dec(t.get("ask")),
            last=_dec(t.get("last")),
            high_24h=_dec(t.get("high")),
            low_24h=_dec(t.get("low")),
            volume_24h=_dec(t.get("baseVolume")),
        )

    def get_trading_rules(self, symbol: str) -> SymbolRulesFx:
        ccxt_symbol = self._market_symbol(symbol)
        market = self.client.markets[ccxt_symbol]
        limits = market.get("limits") or {}

        def lim(category: str, side: str, default: str) -> Decimal:
            return _dec((limits.get(category) or {}).get(side), default)

        precision = market.get("precision") or {}
        return SymbolRulesFx(
            base=market.get("base") or "",
            quote=market.get("quote") or "",
            step_size=_dec(precision.get("amount"), "0.00000001"),
            min_qty=lim("amount", "min", "0"),
            min_notional=lim("cost", "min", "0"),
            tick_size=_dec(precision.get("price"), "0.00000001"),
            price_min=lim("price", "min", "0.00000001"),
            price_max=lim("price", "max", "1000000000"),
            maker_fee=_dec(market.get("maker"), "0.001"),
            taker_fee=_dec(market.get("taker"), "0.001"),
        )

    def get_open_orders(self, symbol: str | None = None) -> list[OpenOrderFx]:
        ccxt_symbol = self._market_symbol(symbol) if symbol else None
        orders = self._call(self.client.fetch_open_orders, ccxt_symbol)
        return [self._to_open_order(o) for o in orders]

    def _to_open_order(self, o: dict[str, Any]) -> OpenOrderFx:
        raw_type = (o.get("type") or "limit").lower()
        mapped: Literal["limit", "stop_limit"] = (
            "stop_limit" if ("stop" in raw_type or "take_profit" in raw_type) else "limit"
        )
        stop_raw = o.get("stopPrice") or o.get("triggerPrice")
        return OpenOrderFx(
            order_id=str(o.get("id") or ""),
            symbol=self._id_of(o.get("symbol")),
            side=o.get("side") or "buy",
            type=mapped,
            qty=_dec(o.get("amount")),
            price=_dec(o.get("price")) if o.get("price") is not None else None,
            stop_price=_dec(stop_raw) if stop_raw is not None else None,
            filled_qty=_dec(o.get("filled")),
        )

    def get_my_trades(
        self, symbol: str | None = None, start: str | None = None, end: str | None = None
    ) -> list[TradeFx]:
        if symbol is None:
            raise ExchangeError("INVALID_SYMBOL", "testnet 查询成交需要提供 symbol（交易所约束）")
        since = _iso_to_ms(start) if start else None
        trades = self._call(self.client.fetch_my_trades, self._market_symbol(symbol), since)
        out: list[TradeFx] = []
        for t in trades:
            ts = t.get("datetime") or ""
            if end and ts and ts > end:
                continue
            fee = t.get("fee") or {}
            out.append(
                TradeFx(
                    trade_id=str(t.get("id") or ""),
                    symbol=symbol,
                    side=t.get("side") or "buy",
                    price=_dec(t.get("price")),
                    qty=_dec(t.get("amount")),
                    fee=_dec(fee.get("cost")),
                    fee_asset=fee.get("currency") or "",
                    timestamp=ts,
                    order_id=str(t["order"]) if t.get("order") else None,
                )
            )
        return out

    def get_transfer_history(
        self, type: str, start: str | None = None, end: str | None = None
    ) -> list[TransferFx]:
        raise ExchangeError(
            "UNSUPPORTED", "spot testnet 不提供充提历史接口（如实报错，不装作空账单）"
        )

    # ------------------------------------------------------------ 可写 ----

    def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        type: Literal["market", "limit", "stop_limit"],
        qty: Money | None = None,
        quote_qty: Money | None = None,
        price: Money | None = None,
        stop_price: Money | None = None,
    ) -> OrderReceipt:
        if type == "stop_limit":
            raise ExchangeError(
                "UNSUPPORTED", "testnet 模式 v0.1 不支持 stop_limit（specs/11 D-l；抽样任务不含）"
            )
        if (qty is None) == (quote_qty is None):
            raise ExchangeError("INVALID_ORDER", "qty 与 quote_qty 必须二选一")
        if quote_qty is not None and type != "market":
            raise ExchangeError("INVALID_ORDER", "quote_qty 仅用于 market 单")
        if type == "limit" and price is None:
            raise ExchangeError("INVALID_ORDER", "limit 单必须给 price")
        if type == "market" and price is not None:
            # ccxt binance 会把 market+price 译成 quoteOrderQty=qty×price（quote 预算单），
            # 与 mock「忽略 price、按 qty 成交」在不可逆写路径上静默分歧——显式拒绝
            raise ExchangeError("INVALID_ORDER", "market 单不接受 price（testnet 语义歧义）")
        ccxt_symbol = self._market_symbol(symbol)
        params: dict[str, Any] = {}
        amount: str | None = None
        if quote_qty is not None:
            params["quoteOrderQty"] = str(quote_qty)  # binance 原生 quote 预算参数
        else:
            # ccxt 精度层（Precise/decimal_to_precision）原生吃十进制字符串——不经 float（R9）
            amount = str(qty)
        order = self._call(
            self.client.create_order,
            ccxt_symbol,
            type,
            side,
            amount,
            str(price) if price is not None else None,
            params,
        )
        return self._to_receipt(
            order, symbol=symbol, side=side, type=type,
            requested_qty=qty, quote_qty=quote_qty, price=price,
        )

    def cancel_order(self, symbol: str, order_id: str) -> OrderReceipt:
        ccxt_symbol = self._market_symbol(symbol)
        order = self._call(self.client.cancel_order, order_id, ccxt_symbol)
        merged = {**order, "status": order.get("status") or "canceled", "id": order.get("id") or order_id}
        raw_type = (order.get("type") or "limit").lower()
        return self._to_receipt(
            merged, symbol=symbol,
            side=order.get("side") or "buy",
            type="market" if raw_type == "market" else "limit",
        )

    def withdraw(
        self, asset: str, amount: Money, address: str, network: str
    ) -> WithdrawReceipt:
        # AC-11c：绝不触碰 ccxt client——testnet 无真实提币，返回模拟回执
        return WithdrawReceipt(
            transfer_id=f"SIM-{next(self._sim_counter)}",
            asset=asset,
            amount=amount,
            address=address,
            network=network,
            status="completed",
            simulated=True,
            timestamp="",
        )

    # ------------------------------------------------------------ 快照 ----

    def _to_receipt(
        self,
        order: dict[str, Any],
        *,
        symbol: str,
        side: Literal["buy", "sell"],
        type: Literal["market", "limit", "stop_limit"],
        requested_qty: Money | None = None,
        quote_qty: Money | None = None,
        price: Money | None = None,
    ) -> OrderReceipt:
        status_map = {
            "open": "new", "closed": "filled",
            "canceled": "canceled", "expired": "canceled", "rejected": "canceled",
        }
        raw_status = order.get("status") or "open"
        filled = _dec(order.get("filled"))
        status = status_map.get(raw_status, "new")
        if raw_status == "open" and filled > 0:
            status = "partially_filled"
        fills: list[Fill] = []
        for t in order.get("trades") or []:
            fee = t.get("fee") or {}
            fills.append(
                Fill(
                    price=_dec(t.get("price")),
                    qty=_dec(t.get("amount")),
                    fee=_dec(fee.get("cost")),
                    fee_asset=fee.get("currency") or "",
                )
            )
        raw_price = order.get("price")
        return OrderReceipt(
            order_id=str(order.get("id") or ""),
            symbol=symbol,
            side=side,
            type=type,
            status=status,
            executed_qty=filled,
            cum_quote=_dec(order.get("cost")),
            fills=fills,
            requested_qty=requested_qty,
            quote_qty=quote_qty,
            price=_dec(raw_price) if raw_price is not None else price,
            stop_price=None,
            created_at=order.get("datetime") or "",
        )

    def export_state(self) -> dict[str, Any]:
        """结构快照（specs/11 D-l）：balances/open_orders 实取；增量对账只在 mock 有意义。"""
        try:
            balances = {
                asset: bal.model_dump(mode="json")
                for asset, bal in sorted(self.get_balances().items())
            }
            open_orders = [o.model_dump(mode="json") for o in self.get_open_orders()]
        except Exception as exc:  # noqa: BLE001 —— 终态导出失败不摧毁已收集的轨迹
            return {
                "balances": {}, "open_orders": [], "new_trades": [], "new_transfers": [],
                "export_error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "balances": balances,
            "open_orders": open_orders,
            "new_trades": [],
            "new_transfers": [],
        }
