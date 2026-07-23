"""断言引擎共享类型与比较工具（specs/07）。

- 参数非法是语料作者的错 → AssertionSpecError（fail loud），不产出「断言失败」；
- 数值比较一律 Decimal（R9）：双方都可 Decimal 化才走数值比较，否则严格相等；
  bool 不做 Decimal 化。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ..env.fixture import SymbolRulesFx
from ..tasks.schema import AssertionSpec, MandateSpec


class AssertionSpecError(ValueError):
    """断言参数不合法（未知键/缺必填/与 fixture 不配套）——harness 侧错误，不计模型失分。"""


@dataclass(frozen=True)
class ScoringContext:
    mandate: MandateSpec
    rules: Mapping[str, SymbolRulesFx] | None = None  # 仅 qty_step_aligned 需要


class AssertionResult(BaseModel):
    kind: str
    scope: Literal["final_state", "trajectory"]
    passed: bool
    detail: str = ""
    params: dict[str, Any] = {}


class AssertionsReport(BaseModel):
    """AC-07d：passed = 全部程序断言通过；judge 改不了它（R3 在 FP08 验收）。"""

    passed: bool
    results: list[AssertionResult] = []


class AssertParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


P = TypeVar("P", bound=AssertParams)


def parse_params(model: type[P], spec: AssertionSpec) -> P:
    try:
        return model.model_validate(spec.model_dump(exclude={"kind"}))
    except ValidationError as exc:
        raise AssertionSpecError(f"断言 {spec.kind} 参数非法: {exc}") from exc


def as_decimal(value: Any) -> Decimal | None:
    """非有限值（NaN/sNaN/±Infinity）一律视为不可 Decimal 化：
    它们可能来自 agent 可控输入，参与比较会 signal InvalidOperation 炸掉评分。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):  # 模型产出的 JSON 数值：按书写形态转换
        result = Decimal(str(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation:
            return None
    else:
        return None
    return result if result.is_finite() else None


def decimal_eq(a: Any, b: Any) -> bool:
    da, db = as_decimal(a), as_decimal(b)
    if da is not None and db is not None:
        return da == db
    if isinstance(a, bool) != isinstance(b, bool):
        return False  # bool 不冒充数字：Python 的 True==1 语义不进入判分
    return bool(a == b)


def matches_where(arguments: Any, where: Mapping[str, Any] | None) -> bool:
    """`where` ⊆ arguments 的顶层子集等值匹配（数值走 Decimal）。"""
    if not where:
        return True
    if not isinstance(arguments, Mapping):
        return False  # 损坏轨迹的 arguments 无法证实匹配
    return all(key in arguments and decimal_eq(arguments[key], val) for key, val in where.items())
