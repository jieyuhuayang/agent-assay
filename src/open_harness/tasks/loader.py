"""YAML → pydantic 加载层（FP02）。数字字符串经 Money 转 Decimal（R9）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..env.fixture import FixtureSpec
from .schema import MandateSpec, TaskSpec


def load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_task(path: Path) -> TaskSpec:
    return TaskSpec.model_validate(load_yaml(path))


def load_mandate(path: Path) -> MandateSpec:
    data = load_yaml(path)
    if isinstance(data, dict) and set(data) == {"mandate"}:
        data = data["mandate"]
    return MandateSpec.model_validate(data)


def load_fixture(path: Path) -> FixtureSpec:
    return FixtureSpec.model_validate(load_yaml(path))
