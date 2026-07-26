"""x402 支付 fixture schema（FP13，specs/13）。数字一律字符串 → Decimal（R9）。

字符串纪律（R6/R2）：pay_to 用 PAYMOCK… 风格 mock 值（不得形似真实链上地址）；
url 用 .example 保留域——只是环境内字典键，任何代码路径都不发真实请求（R1）。
content 是红队注入语料的载体字段（对应交易所侧 C08 的 memo）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, model_validator

from ..money import Money
from .fixture import AssetBalance


class X402BehaviorsFx(BaseModel):
    double_charge: bool = False  # 付款成功后首次 re-fetch 谎称未收到并再 402 一次（X07）
    paywall_despite_free: bool = False  # 语义上免费的资源机会性 402（语料标注，X08）


class X402ResourceFx(BaseModel):
    url: str
    price: Money | None = None  # None = 免费（fetch 直接返回内容）
    asset: str = "USDC"
    network: str = "mocknet"
    pay_to: str = ""
    description: str = ""
    content: str
    answer: dict[str, Any] | None = None  # 语料作者备查（任务 report_answer 期望值）
    behaviors: X402BehaviorsFx = X402BehaviorsFx()

    @model_validator(mode="after")
    def _coherent(self):
        if self.price is not None and self.price <= 0:
            raise ValueError(f"{self.url}: price 必须 > 0（免费用 price: null）")
        if self.price is not None and not self.pay_to:
            raise ValueError(f"{self.url}: 付费资源必须给 pay_to")
        if self.behaviors.paywall_despite_free and self.price is None:
            raise ValueError(f"{self.url}: paywall_despite_free 的资源必须带 price（机会性收费才成立）")
        if self.behaviors.double_charge and self.price is None:
            raise ValueError(f"{self.url}: double_charge 的资源必须带 price")
        return self


class PaymentFx(BaseModel):
    """存量支付流水（X04 对账素材）。评分的 new_payments 增量不含它们。"""

    payment_id: str
    url: str
    amount: Money
    asset: str = "USDC"
    pay_to: str
    timestamp: str  # ISO-8601 UTC


class X402FixtureSpec(BaseModel):
    kind: Literal["x402"]
    wallet: dict[str, AssetBalance]
    resources: list[X402ResourceFx]
    payments: list[PaymentFx] = []
    start_time: str = "2026-07-20T00:00:00Z"  # 逻辑时钟起点（R4 确定性）

    @model_validator(mode="after")
    def _coherent(self):
        urls = [r.url for r in self.resources]
        duplicates = sorted({u for u in urls if urls.count(u) > 1})
        if duplicates:
            raise ValueError(f"resources url 重复: {duplicates}")
        for asset, balance in self.wallet.items():
            if balance.free < 0 or balance.locked < 0:
                raise ValueError(f"wallet[{asset}] 余额不得为负")
        return self
