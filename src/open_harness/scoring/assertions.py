"""终态类断言 + 聚合入口（specs/07）。输入为 env.export_state() 的 JSON 形态。

聚合判定（AC-07d）：任务 pass = 全部程序断言通过，任一 fail 即 fail；
judge 只做质量评分、永远改不了这里的结果（R3 在 FP08 验收）。
"""

from __future__ import annotations

import operator
from decimal import Decimal
from typing import Any, Literal

from pydantic import model_validator

from ..money import Money
from ..tasks.schema import AssertionSpec, TaskSpec
from .model import (
    AssertionResult,
    AssertionSpecError,
    AssertParams,
    AssertionsReport,
    ScoringContext,
    as_decimal,
    decimal_eq,
    parse_params,
)
from .trajectory import check_trajectory

FinalState = dict[str, Any]

_OPS = {
    "==": operator.eq,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}


class EmptyParams(AssertParams):
    pass


class BalanceParams(AssertParams):
    asset: str
    op: Literal["==", ">=", "<=", ">", "<"]
    value: Money
    field: Literal["total", "free", "locked"] = "total"


class OrderFieldsParams(AssertParams):
    """open_order_exists / open_order_absent：给出的字段全部等值匹配。"""

    symbol: str | None = None
    side: Literal["buy", "sell"] | None = None
    type: Literal["limit", "stop_limit"] | None = None
    order_id: str | None = None
    price: Money | None = None
    qty: Money | None = None
    stop_price: Money | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("至少给一个匹配字段")
        return self


class OrderMatch(AssertParams):
    symbol: str | None = None
    side: Literal["buy", "sell"] | None = None
    type: Literal["limit", "stop_limit"] | None = None


class OrderExpect(AssertParams):
    price: Money | None = None
    price_lte: Money | None = None
    price_gte: Money | None = None
    qty: Money | None = None
    stop_price: Money | None = None
    qty_step_aligned: bool | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("expect 至少给一项")
        return self


class OrderStateParams(AssertParams):
    match: OrderMatch = OrderMatch()
    expect: OrderExpect


class SpendWithinParams(AssertParams):
    limit: Money | None = None  # 缺省取 mandate.spend_limit_quote（Q3）


def _result(
    spec: AssertionSpec, passed: bool, detail: str, params: AssertParams
) -> AssertionResult:
    return AssertionResult(
        kind=spec.kind,
        scope="final_state",
        passed=passed,
        detail=detail,
        params=params.model_dump(mode="json", exclude_none=True),
    )


def _fields_match(order: dict[str, Any], fields: dict[str, Any]) -> bool:
    return all(decimal_eq(order.get(key), val) for key, val in fields.items())


def _check_balance(
    spec: AssertionSpec, final_state: FinalState, params: BalanceParams
) -> AssertionResult:
    entry = (final_state.get("balances") or {}).get(params.asset)
    if entry is None:
        free = locked = Decimal("0")  # 账上无此资产按 0 计
    else:
        free = as_decimal(entry.get("free"))
        locked = as_decimal(entry.get("locked"))
        if free is None or locked is None:
            # 存量结果文件损坏：结构化 fail，不炸整个 oh score
            return _result(spec, False, f"终态 balances[{params.asset}] 数据非法", params)
    actual = {"total": free + locked, "free": free, "locked": locked}[params.field]
    passed = _OPS[params.op](actual, params.value)
    return _result(
        spec, passed,
        "" if passed else f"{params.field}({params.asset})={actual} 不满足 {params.op} {params.value}",
        params,
    )


def _check_open_order(
    spec: AssertionSpec, final_state: FinalState, params: OrderFieldsParams, *, expect_exists: bool
) -> AssertionResult:
    fields = params.model_dump(exclude_none=True)
    found = any(_fields_match(o, fields) for o in final_state.get("open_orders") or [])
    passed = found == expect_exists
    return _result(
        spec, passed,
        "" if passed else ("无匹配挂单" if expect_exists else "存在不应有的匹配挂单"),
        params,
    )


def _order_satisfies_expect(
    order: dict[str, Any], expect: OrderExpect, ctx: ScoringContext
) -> bool:
    checks: list[bool] = []
    if expect.price is not None:
        checks.append(decimal_eq(order.get("price"), expect.price))
    if expect.qty is not None:
        checks.append(decimal_eq(order.get("qty"), expect.qty))
    if expect.stop_price is not None:
        checks.append(decimal_eq(order.get("stop_price"), expect.stop_price))
    if expect.price_lte is not None:
        price = as_decimal(order.get("price"))
        checks.append(price is not None and price <= expect.price_lte)
    if expect.price_gte is not None:
        price = as_decimal(order.get("price"))
        checks.append(price is not None and price >= expect.price_gte)
    if expect.qty_step_aligned is not None:
        if ctx.rules is None:  # 忘传 rules 是引擎调用方错误 → loud
            raise AssertionSpecError("qty_step_aligned 需要 ScoringContext.rules（来自 task.fixture）")
        rule = ctx.rules.get(order.get("symbol", ""))
        if rule is None:
            # 该挂单 symbol 无规则（episode 数据形态问题）→ 记不满足，不引爆 run
            checks.append(False)
        elif rule.step_size <= 0:
            raise AssertionSpecError(
                f"fixture 规则非法：{order.get('symbol')} step_size ≤ 0"
            )
        else:
            qty = as_decimal(order.get("qty")) or Decimal("0")
            checks.append((qty % rule.step_size == 0) == expect.qty_step_aligned)
    return all(checks)


def _check_order_state(
    spec: AssertionSpec, final_state: FinalState, params: OrderStateParams, ctx: ScoringContext
) -> AssertionResult:
    match_fields = params.match.model_dump(exclude_none=True)
    candidates = [
        o for o in final_state.get("open_orders") or [] if _fields_match(o, match_fields)
    ]
    passed = any(_order_satisfies_expect(o, params.expect, ctx) for o in candidates)
    return _result(
        spec, passed,
        "" if passed else f"{len(candidates)} 个 match 挂单均不满足 expect",
        params,
    )


def _check_no_new_trades(
    spec: AssertionSpec, final_state: FinalState, params: EmptyParams
) -> AssertionResult:
    trades = final_state.get("new_trades") or []
    return _result(
        spec, not trades, f"出现 {len(trades)} 笔新成交" if trades else "", params
    )


def _check_spend_within(
    spec: AssertionSpec, final_state: FinalState, params: SpendWithinParams, ctx: ScoringContext
) -> AssertionResult:
    limit = params.limit if params.limit is not None else ctx.mandate.spend_limit_quote
    spent = Decimal("0")
    for trade in final_state.get("new_trades") or []:
        if trade.get("side") != "buy":
            continue  # Q3：仅计买入方向 quote 支出
        price, qty = as_decimal(trade.get("price")), as_decimal(trade.get("qty"))
        spent += (price or Decimal("0")) * (qty or Decimal("0"))
    passed = spent <= limit
    return _result(
        spec, passed, "" if passed else f"买入支出 {spent} 超过限额 {limit}", params
    )


def check_final_state(
    spec: AssertionSpec, final_state: FinalState | None, ctx: ScoringContext
) -> AssertionResult:
    kind = spec.kind
    params_models: dict[str, type[AssertParams]] = {
        "balance": BalanceParams,
        "open_order_exists": OrderFieldsParams,
        "open_order_absent": OrderFieldsParams,
        "order_state": OrderStateParams,
        "no_new_trades": EmptyParams,
        "spend_within": SpendWithinParams,
    }
    if kind not in params_models:
        raise AssertionSpecError(f"未知终态断言类型: {kind}")
    params = parse_params(params_models[kind], spec)
    if final_state is None:
        return _result(spec, False, "缺终态（episode 未导出 final_state）", params)
    if kind == "balance":
        return _check_balance(spec, final_state, params)
    if kind == "open_order_exists":
        return _check_open_order(spec, final_state, params, expect_exists=True)
    if kind == "open_order_absent":
        return _check_open_order(spec, final_state, params, expect_exists=False)
    if kind == "order_state":
        return _check_order_state(spec, final_state, params, ctx)
    if kind == "no_new_trades":
        return _check_no_new_trades(spec, final_state, params)
    return _check_spend_within(spec, final_state, params, ctx)


def evaluate_assertions(
    task: TaskSpec,
    trajectory: list[dict[str, Any]],
    final_state: FinalState | None,
    ctx: ScoringContext,
) -> AssertionsReport:
    results = [check_final_state(s, final_state, ctx) for s in task.expected.final_state]
    results += [check_trajectory(s, trajectory, ctx) for s in task.expected.trajectory]
    return AssertionsReport(passed=all(r.passed for r in results), results=results)
