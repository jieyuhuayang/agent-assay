"""结果 schema：环境指纹必填（红线 R11）。

trajectory / final_state / scoring 在 FP01 先留宽类型，由 FP05 / FP07 / FP08 收紧；
M1 过渡形态 scoring=None，评分由 ``oh score`` 回填（specs/00 · Q1(a)）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

EpisodeStatus = Literal["done", "blocked", "max_steps", "timeout", "infra_error"]


class Fingerprint(BaseModel):
    """环境指纹（R11 必填全集）。temperature 存字符串，避免 float 进结果 JSON。"""

    model_config = ConfigDict(protected_namespaces=(), extra="forbid")

    model: str
    model_version: str
    taskset_version: str
    git_commit: str
    timestamp: str  # ISO-8601；回放一致性比较时按 Q5 白名单剥离
    temperature: str


class ResultRecord(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    task_id: str
    status: EpisodeStatus
    fingerprint: Fingerprint
    trajectory: list[Any] = []
    final_state: dict[str, Any] | None = None
    scoring: dict[str, Any] | None = None
