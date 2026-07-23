"""确定性 mock 交易所（FP03，撮合规则见 specs/03 / KICKOFF 第 10 节）。

- 市价单按对手价立即成交（可配滑点 bp 与部分成交脚本）；
- 限价单穿越对手价按对手价成交，否则入簿冻结；stop_limit 挂起不触发；
- 费率从收到资产扣；每次可写操作后跑 invariant 检查（违反即 raise）；
- 逻辑时钟 + 递增 id，保证可重放（R4）；
- D3：不做任何 mandate 维度检查。
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Literal

from .base import (
    ExchangeEnv,
    ExchangeError,
    Fill,
    InvariantViolation,
    OrderReceipt,
    WithdrawReceipt,
)
from .fixture import (
    AssetBalance,
    FixtureSpec,
    OpenOrderFx,
    SymbolRulesFx,
    TickerFx,
    TradeFx,
    TransferFx,
)

_ZERO = Decimal("0")
_BP = Decimal("10000")


def _floor_to(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _ceil_to(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def _aligned(value: Decimal, step: Decimal) -> bool:
    return value == _floor_to(value, step)


class MockExchangeEnv(ExchangeEnv):
    def __init__(self, fixture: FixtureSpec) -> None:
        fx = fixture.model_copy(deep=True)
        self._balances: dict[str, AssetBalance] = fx.balances
        self._rules: dict[str, SymbolRulesFx] = fx.rules
        self._tickers: dict[str, TickerFx] = fx.tickers
        self._orders: dict[str, OpenOrderFx] = {o.order_id: o for o in fx.open_orders}
        self._trades: list[TradeFx] = fx.trades
        self._transfers: list[TransferFx] = fx.transfers
        self._partial_fills = list(fx.mock.partial_fills)
        self._slippage_bp: Decimal = fx.mock.slippage_bp

        self._t0 = datetime.fromisoformat(fx.mock.start_time.replace("Z", "+00:00"))
        self._tick = 0
        self._order_ids = itertools.count(5001)
        self._trade_ids = itertools.count(9001)
        self._transfer_ids = itertools.count(7001)

        self._initial_trades = len(self._trades)
        self._initial_transfers = len(self._transfers)

        self._check_invariants()  # fixture 自洽性（AC-03g）

    # ------------------------------------------------------------ 只读 ----

    def get_balances(self) -> dict[str, AssetBalance]:
        return {a: b.model_copy() for a, b in self._balances.items()}

    def get_ticker(self, symbol: str) -> TickerFx:
        return self._ticker(symbol).model_copy()

    def get_trading_rules(self, symbol: str) -> SymbolRulesFx:
        return self._rule(symbol).model_copy()

    def get_open_orders(self, symbol: str | None = None) -> list[OpenOrderFx]:
        orders = [o.model_copy() for o in self._orders.values()]
        if symbol is not None:
            self._rule(symbol)  # 未知 symbol 同样报 INVALID_SYMBOL
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def get_my_trades(
        self, symbol: str | None = None, start: str | None = None, end: str | None = None
    ) -> list[TradeFx]:
        if symbol is not None:
            self._rule(symbol)
        rows = [t.model_copy() for t in self._trades]
        if symbol is not None:
            rows = [t for t in rows if t.symbol == symbol]
        return _filter_window(rows, start, end)

    def get_transfer_history(
        self, type: str, start: str | None = None, end: str | None = None
    ) -> list[TransferFx]:
        if type not in ("deposit", "withdraw"):
            raise ExchangeError("INVALID_ORDER", f"未知转账类型 {type!r}（deposit/withdraw）")
        rows = [t.model_copy() for t in self._transfers if t.type == type]
        return _filter_window(rows, start, end)

    # ------------------------------------------------------------ 可写 ----

    def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        type: Literal["market", "limit", "stop_limit"],
        qty: Decimal | None = None,
        quote_qty: Decimal | None = None,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> OrderReceipt:
        rule = self._rule(symbol)
        self._validate_order_shape(type, qty, quote_qty, price, stop_price)
        self._validate_filters(rule, type, qty, quote_qty, price, stop_price)

        if type == "market":
            receipt = self._execute_market(symbol, rule, side, qty, quote_qty)
        elif type == "limit" and self._crosses(symbol, side, price):
            receipt = self._execute_taker_limit(symbol, rule, side, qty, price)
        else:  # 入簿：不穿越的 limit，或挂起的 stop_limit
            receipt = self._rest_order(symbol, rule, side, type, qty, price, stop_price)

        self._check_invariants()
        return receipt

    def cancel_order(self, symbol: str, order_id: str) -> OrderReceipt:
        self._rule(symbol)
        order = self._orders.get(order_id)
        if order is None or order.symbol != symbol:
            raise ExchangeError("UNKNOWN_ORDER", f"挂单不存在: {order_id} ({symbol})")
        rule = self._rule(order.symbol)
        remaining = order.qty - order.filled_qty
        if order.side == "buy":
            assert order.price is not None
            self._unlock(rule.quote, remaining * order.price)
        else:
            self._unlock(rule.base, remaining)
        del self._orders[order_id]
        self._check_invariants()
        return OrderReceipt(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            type=order.type,
            status="canceled",
            executed_qty=order.filled_qty,
            cum_quote=_ZERO,
            requested_qty=order.qty,
            price=order.price,
            stop_price=order.stop_price,
            created_at=self._next_ts(),
        )

    def withdraw(
        self, asset: str, amount: Decimal, address: str, network: str
    ) -> WithdrawReceipt:
        # D3：地址/网络不做白名单校验，原样执行并记录
        balance = self._balances.get(asset)
        if balance is None or balance.free < amount:
            free = balance.free if balance else _ZERO
            raise ExchangeError(
                "INSUFFICIENT_BALANCE", f"{asset} 可用余额不足: free={free}, 请求={amount}"
            )
        if amount <= _ZERO:
            raise ExchangeError("INVALID_ORDER", "提币数量必须为正")
        balance.free -= amount
        ts = self._next_ts()
        transfer = TransferFx(
            transfer_id=f"W-{next(self._transfer_ids)}",
            type="withdraw",
            asset=asset,
            amount=amount,
            timestamp=ts,
            address=address,
            network=network,
        )
        self._transfers.append(transfer)
        self._check_invariants()
        return WithdrawReceipt(
            transfer_id=transfer.transfer_id,
            asset=asset,
            amount=amount,
            address=address,
            network=network,
            timestamp=ts,
        )

    # ------------------------------------------------------------ 快照 ----

    def export_state(self) -> dict[str, Any]:
        return {
            "balances": {
                a: b.model_dump(mode="json") for a, b in sorted(self._balances.items())
            },
            "open_orders": [o.model_dump(mode="json") for o in self._orders.values()],
            "new_trades": [
                t.model_dump(mode="json") for t in self._trades[self._initial_trades :]
            ],
            "new_transfers": [
                t.model_dump(mode="json") for t in self._transfers[self._initial_transfers :]
            ],
        }

    # ------------------------------------------------------------ 内部 ----

    def _rule(self, symbol: str) -> SymbolRulesFx:
        rule = self._rules.get(symbol)
        if rule is None:
            raise ExchangeError("INVALID_SYMBOL", f"未知交易对: {symbol}")
        return rule

    def _ticker(self, symbol: str) -> TickerFx:
        self._rule(symbol)
        ticker = self._tickers.get(symbol)
        if ticker is None:
            raise ExchangeError("INVALID_SYMBOL", f"无行情快照: {symbol}")
        return ticker

    @staticmethod
    def _validate_order_shape(
        type: str,
        qty: Decimal | None,
        quote_qty: Decimal | None,
        price: Decimal | None,
        stop_price: Decimal | None,
    ) -> None:
        if (qty is None) == (quote_qty is None):
            raise ExchangeError("INVALID_ORDER", "qty 与 quote_qty 必须二选一")
        if quote_qty is not None and type != "market":
            raise ExchangeError("INVALID_ORDER", "quote_qty 仅市价单可用")
        if type in ("limit", "stop_limit") and price is None:
            raise ExchangeError("INVALID_ORDER", f"{type} 单必须提供 price")
        if type == "stop_limit" and stop_price is None:
            raise ExchangeError("INVALID_ORDER", "stop_limit 单必须提供 stop_price")
        if type == "market" and (price is not None or stop_price is not None):
            raise ExchangeError("INVALID_ORDER", "市价单不接受 price/stop_price")
        for name, value in (("qty", qty), ("quote_qty", quote_qty), ("price", price), ("stop_price", stop_price)):
            if value is not None and value <= _ZERO:
                raise ExchangeError("INVALID_ORDER", f"{name} 必须为正")

    def _validate_filters(
        self,
        rule: SymbolRulesFx,
        type: str,
        qty: Decimal | None,
        quote_qty: Decimal | None,
        price: Decimal | None,
        stop_price: Decimal | None,
    ) -> None:
        if qty is not None:
            if qty < rule.min_qty:
                raise ExchangeError("LOT_SIZE", f"qty {qty} < minQty {rule.min_qty}")
            if not _aligned(qty, rule.step_size):
                raise ExchangeError("LOT_SIZE", f"qty {qty} 不是 stepSize {rule.step_size} 的整数倍")
        for name, p in (("price", price), ("stop_price", stop_price)):
            if p is None:
                continue
            if not (rule.price_min <= p <= rule.price_max):
                raise ExchangeError("PRICE_FILTER", f"{name} {p} 超出 [{rule.price_min}, {rule.price_max}]")
            if not _aligned(p, rule.tick_size):
                raise ExchangeError("PRICE_FILTER", f"{name} {p} 不是 tickSize {rule.tick_size} 的整数倍")

        if quote_qty is not None:
            notional = quote_qty
        elif type == "market":
            assert qty is not None
            ticker = self._tickers.get(rule.base + rule.quote)
            ref = ticker.last if ticker else _ZERO
            notional = qty * ref
        else:
            assert qty is not None and price is not None
            notional = qty * price
        if notional < rule.min_notional:
            raise ExchangeError(
                "MIN_NOTIONAL", f"名义额 {notional} < minNotional {rule.min_notional}"
            )

    def _crosses(self, symbol: str, side: str, price: Decimal | None) -> bool:
        assert price is not None
        ticker = self._ticker(symbol)
        return price >= ticker.ask if side == "buy" else price <= ticker.bid

    def _exec_price(self, symbol: str, rule: SymbolRulesFx, side: str, slip: bool) -> Decimal:
        ticker = self._ticker(symbol)
        base_price = ticker.ask if side == "buy" else ticker.bid
        if not slip or self._slippage_bp == _ZERO:
            return base_price
        factor = self._slippage_bp / _BP
        if side == "buy":
            return _ceil_to(base_price * (1 + factor), rule.tick_size)
        return _floor_to(base_price * (1 - factor), rule.tick_size)

    def _consume_partial_fill(self, symbol: str, side: str) -> Decimal | None:
        for i, r in enumerate(self._partial_fills):
            if (r.symbol is None or r.symbol == symbol) and (r.side is None or r.side == side):
                del self._partial_fills[i]
                return r.ratio
        return None

    def _execute_market(
        self,
        symbol: str,
        rule: SymbolRulesFx,
        side: str,
        qty: Decimal | None,
        quote_qty: Decimal | None,
    ) -> OrderReceipt:
        exec_price = self._exec_price(symbol, rule, side, slip=True)
        if qty is None:  # 市价按 quote 额买入（A06）
            assert quote_qty is not None and side == "buy"
            target_qty = _floor_to(quote_qty / exec_price, rule.step_size)
            if target_qty <= _ZERO:
                raise ExchangeError("LOT_SIZE", f"quote_qty {quote_qty} 折算数量不足一个 stepSize")
        else:
            target_qty = qty

        ratio = self._consume_partial_fill(symbol, side)
        fill_qty = target_qty if ratio is None else _floor_to(target_qty * ratio, rule.step_size)
        if fill_qty <= _ZERO:
            raise ExchangeError("LOT_SIZE", "部分成交脚本折算后成交量为 0")
        status = "filled" if fill_qty == target_qty else "partially_filled"
        return self._settle_taker_fill(
            symbol, rule, side, "market", fill_qty, exec_price,
            requested_qty=qty, quote_qty=quote_qty, status=status,
        )

    def _execute_taker_limit(
        self, symbol: str, rule: SymbolRulesFx, side: str, qty: Decimal | None, price: Decimal | None
    ) -> OrderReceipt:
        assert qty is not None and price is not None
        exec_price = self._exec_price(symbol, rule, side, slip=False)  # 穿越限价按对手价，无滑点
        receipt = self._settle_taker_fill(
            symbol, rule, side, "limit", qty, exec_price,
            requested_qty=qty, quote_qty=None, status="filled",
        )
        receipt.price = price
        return receipt

    def _settle_taker_fill(
        self,
        symbol: str,
        rule: SymbolRulesFx,
        side: str,
        type: str,
        fill_qty: Decimal,
        exec_price: Decimal,
        *,
        requested_qty: Decimal | None,
        quote_qty: Decimal | None,
        status: str,
    ) -> OrderReceipt:
        quote_amount = fill_qty * exec_price
        if side == "buy":
            self._debit_free(rule.quote, quote_amount)
            fee = fill_qty * rule.taker_fee
            self._credit_free(rule.base, fill_qty - fee)
            fee_asset = rule.base
        else:
            self._debit_free(rule.base, fill_qty)
            fee = quote_amount * rule.taker_fee
            self._credit_free(rule.quote, quote_amount - fee)
            fee_asset = rule.quote

        ts = self._next_ts()
        order_id = f"OH-{next(self._order_ids)}"
        self._trades.append(
            TradeFx(
                trade_id=f"T-{next(self._trade_ids)}",
                symbol=symbol,
                side=side,  # type: ignore[arg-type]
                price=exec_price,
                qty=fill_qty,
                fee=fee,
                fee_asset=fee_asset,
                timestamp=ts,
                order_id=order_id,
            )
        )
        return OrderReceipt(
            order_id=order_id,
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            type=type,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            executed_qty=fill_qty,
            cum_quote=quote_amount,
            fills=[Fill(price=exec_price, qty=fill_qty, fee=fee, fee_asset=fee_asset)],
            requested_qty=requested_qty,
            quote_qty=quote_qty,
            created_at=ts,
        )

    def _rest_order(
        self,
        symbol: str,
        rule: SymbolRulesFx,
        side: str,
        type: str,
        qty: Decimal | None,
        price: Decimal | None,
        stop_price: Decimal | None,
    ) -> OrderReceipt:
        assert qty is not None and price is not None
        if side == "buy":
            self._lock(rule.quote, qty * price)
        else:
            self._lock(rule.base, qty)
        ts = self._next_ts()
        order = OpenOrderFx(
            order_id=f"OH-{next(self._order_ids)}",
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            type=type,  # type: ignore[arg-type]
            qty=qty,
            price=price,
            stop_price=stop_price,
        )
        self._orders[order.order_id] = order
        return OrderReceipt(
            order_id=order.order_id,
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            type=type,  # type: ignore[arg-type]
            status="new",
            executed_qty=_ZERO,
            cum_quote=_ZERO,
            requested_qty=qty,
            price=price,
            stop_price=stop_price,
            created_at=ts,
        )

    # ---- 记账原语 ----

    def _asset(self, asset: str) -> AssetBalance:
        return self._balances.setdefault(asset, AssetBalance(free=_ZERO, locked=_ZERO))

    def _debit_free(self, asset: str, amount: Decimal) -> None:
        balance = self._asset(asset)
        if balance.free < amount:
            raise ExchangeError(
                "INSUFFICIENT_BALANCE", f"{asset} 可用余额不足: free={balance.free}, 需要={amount}"
            )
        balance.free -= amount

    def _credit_free(self, asset: str, amount: Decimal) -> None:
        self._asset(asset).free += amount

    def _lock(self, asset: str, amount: Decimal) -> None:
        self._debit_free(asset, amount)
        self._asset(asset).locked += amount

    def _unlock(self, asset: str, amount: Decimal) -> None:
        balance = self._asset(asset)
        if balance.locked < amount:
            raise InvariantViolation(f"{asset} 解冻超额: locked={balance.locked}, 解冻={amount}")
        balance.locked -= amount
        balance.free += amount

    def _next_ts(self) -> str:
        self._tick += 1
        return (self._t0 + timedelta(seconds=self._tick)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _check_invariants(self) -> None:
        expected_locked: dict[str, Decimal] = {}
        for order in self._orders.values():
            rule = self._rules.get(order.symbol)
            if rule is None:
                raise InvariantViolation(f"挂单 {order.order_id} 的 symbol 无交易规则: {order.symbol}")
            remaining = order.qty - order.filled_qty
            if order.side == "buy":
                if order.price is None:
                    raise InvariantViolation(f"买单 {order.order_id} 无价格，无法计算冻结")
                asset, amount = rule.quote, remaining * order.price
            else:
                asset, amount = rule.base, remaining
            expected_locked[asset] = expected_locked.get(asset, _ZERO) + amount

        for asset, balance in self._balances.items():
            if balance.free < _ZERO or balance.locked < _ZERO:
                raise InvariantViolation(f"{asset} 余额为负: free={balance.free}, locked={balance.locked}")
            expected = expected_locked.get(asset, _ZERO)
            if balance.locked != expected:
                raise InvariantViolation(
                    f"{asset} 冻结不守恒: locked={balance.locked}, 挂单应冻结={expected}"
                )
        for asset, expected in expected_locked.items():
            if asset not in self._balances:
                raise InvariantViolation(f"{asset} 有挂单冻结 {expected} 但无余额条目")


def _filter_window(rows: list, start: str | None, end: str | None) -> list:
    """ISO-8601 UTC 字符串按字典序过滤 [start, end]（含端点）。"""
    if start is not None:
        rows = [r for r in rows if r.timestamp >= start]
    if end is not None:
        rows = [r for r in rows if r.timestamp <= end]
    return rows
