"""YAML → pydantic 加载层（FP02）。数字字符串经 Money 转 Decimal（R9）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..env.fixture import FixtureSpec
from .schema import MandateSpec, TaskSpec


class OhYamlLoader(yaml.SafeLoader):
    """YAML 1.2 语义的布尔：仅 true/false。

    YAML 1.1 会把 on/off/yes/no 解析成布尔——而任务 schema 的 user_script 用 `on:` 做键
    （KICKOFF 7.1），必须保持字符串。
    """


for _ch in "OoYyNn":
    if _ch in OhYamlLoader.yaml_implicit_resolvers:
        OhYamlLoader.yaml_implicit_resolvers[_ch] = [
            (tag, regexp)
            for tag, regexp in OhYamlLoader.yaml_implicit_resolvers[_ch]
            if tag != "tag:yaml.org,2002:bool"
        ]


def load_yaml_text(text: str) -> Any:
    return yaml.load(text, Loader=OhYamlLoader)


def load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.load(fh, Loader=OhYamlLoader)


def load_task(path: Path) -> TaskSpec:
    return TaskSpec.model_validate(load_yaml(path))


def _kind_of(data: object, path: Path) -> str:
    """顶层 kind 判别（specs/13 D-o）：缺省 exchange（旧 YAML 零翻新），未知即报错。"""
    kind = data.get("kind", "exchange") if isinstance(data, dict) else "exchange"
    if kind not in ("exchange", "x402"):
        raise ValueError(f"{path}: 未知 kind: {kind!r}（支持 exchange | x402）")
    return kind


def load_mandate(path: Path) -> "AnyMandate":
    data = load_yaml(path)
    if isinstance(data, dict) and set(data) == {"mandate"}:
        data = data["mandate"]
    if _kind_of(data, path) == "x402":
        from .schema import X402MandateSpec

        return X402MandateSpec.model_validate(data)
    return MandateSpec.model_validate(data)


def load_fixture(path: Path) -> "FixtureSpec | X402FixtureSpec":
    data = load_yaml(path)
    if _kind_of(data, path) == "x402":
        from ..env.x402_fixture import X402FixtureSpec

        return X402FixtureSpec.model_validate(data)
    return FixtureSpec.model_validate(data)
