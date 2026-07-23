"""Money 类型：资金数字全程 Decimal（红线 R9），YAML/JSON 中为字符串。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer


def to_decimal(value: Any) -> Decimal:
    """str/int/Decimal → Decimal；float 与 bool 一律拒绝（R9）。"""
    if isinstance(value, bool):
        raise ValueError("bool 不是资金数字")
    if isinstance(value, float):
        raise ValueError("资金数字禁止 float（R9）：请用字符串，如 \"0.1\"")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"非法的 Decimal 字符串: {value!r}") from exc
    raise ValueError(f"无法把 {type(value).__name__} 转为 Decimal")


Money = Annotated[
    Decimal,
    BeforeValidator(to_decimal),
    PlainSerializer(str, return_type=str, when_used="json"),
]
