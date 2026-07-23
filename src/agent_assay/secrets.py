"""API key 只经环境变量进入（红线 R2），并提供日志/落盘前的脱敏。"""

from __future__ import annotations

import os

TESTNET_API_KEY_ENV = "OH_TESTNET_API_KEY"
TESTNET_API_SECRET_ENV = "OH_TESTNET_API_SECRET"

_SECRET_NAME_TOKENS = ("KEY", "SECRET", "TOKEN", "PASSWORD")
_MIN_SECRET_LEN = 6  # 更短的值做替换会误伤普通文本


def get_secret(name: str) -> str | None:
    """secret 的唯一入口：读环境变量，绝不读文件/配置（R2）。"""
    return os.environ.get(name)


def secret_values() -> list[str]:
    """当前进程环境里所有疑似 secret 的值（按变量名 KEY/SECRET/TOKEN/PASSWORD 判定）。"""
    return [
        value
        for name, value in os.environ.items()
        if value
        and len(value) >= _MIN_SECRET_LEN
        and any(token in name.upper() for token in _SECRET_NAME_TOKENS)
    ]


def redact(text: str) -> str:
    """把文本中出现的 secret 值替换为 ***；日志与结果 JSON 落盘前必须调用。"""
    for value in secret_values():
        if value in text:
            text = text.replace(value, "***")
    return text
