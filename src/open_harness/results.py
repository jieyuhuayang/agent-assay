"""结果 schema：环境指纹必填（红线 R11）。

trajectory / final_state / scoring 在 FP01 先留宽类型，由 FP05 / FP07 / FP08 收紧；
scoring 自 FP08 起由 ``oh run`` 内联填充（AC-08g），``oh score`` 可离线重评/补跑 judge。
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
    transcript: list[dict[str, Any]] = []
    timing: dict[str, Any] | None = None  # 易变字段：回放比较时按 Q5 白名单剥离


def save_result(record: ResultRecord, path: Any) -> None:
    """结果落盘的唯一路径：序列化后必过脱敏（R2）。"""
    from pathlib import Path

    from .secrets import redact

    text = redact(record.model_dump_json(indent=2))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")
