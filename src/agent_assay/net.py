"""集中网络封装（红线 R1）：交易所侧出站流量的唯一闸口。

管辖边界（specs/00 · 5.2 D-a）：
- 交易所侧：任何交易所 URL 使用前必须过 ``check_url``；白名单只有 testnet。
  ccxt 路径（FP11）的接入方式是「base URL 经本层校验/注入」，不是流量代理。
- 模型侧：litellm 域名无法静态枚举，不走本层；改为结构性约束——模型/HTTP 库
  只允许 agent/providers.py 与 scoring/judge.py import（守护测试
  ``test_r1_model_calls_only_from_provider_modules``）。
"""

from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_EXCHANGE_HOSTS = frozenset({"testnet.binance.vision"})


class ForbiddenHostError(RuntimeError):
    """URL 的 host 不在交易所侧白名单内（R1：任何代码路径不得触达主网）。"""


def check_url(url: str) -> str:
    """校验交易所侧 URL；通过则原样返回，否则 raise ForbiddenHostError。"""
    host = urlparse(url).hostname
    if host is None or host.lower() not in ALLOWED_EXCHANGE_HOSTS:
        raise ForbiddenHostError(
            f"host {host!r} 不在交易所侧白名单 {sorted(ALLOWED_EXCHANGE_HOSTS)} 内（R1）"
        )
    return url
