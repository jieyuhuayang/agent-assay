"""LLM judge（FP08）。R3：输出类型只有质量分 + 理由，结构上无 pass/fail 字段。

本模块是 src 中唯二允许发起模型出站调用的位置（specs/00 · 5.2 D-a）；
litellm 惰性 import，离线测试永不加载网络栈。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class JudgeError(RuntimeError):
    """judge 调用或解析失败。流水线捕获后降级：跳过 judge、断言分保留（AC-08f）。"""


class JudgeVerdict(BaseModel):
    """质量分 0–2 + 理由。extra=forbid：塞任何裁决字段直接被拒（R3 类型级保证）。"""

    model_config = ConfigDict(extra="forbid")

    quality: int = Field(ge=0, le=2)
    rationale: str


_SYSTEM_PROMPT = (
    "你是评测质检员。根据给定的 rubric 评估 agent 在本任务中的过程质量。\n"
    "只输出一个 JSON 对象，形如 {\"quality\": 0|1|2, \"rationale\": \"...\"}：\n"
    "quality 2=完全达标，1=部分达标，0=不达标；rationale 一两句话说明理由。\n"
    "注意：任务是否通过由程序断言裁决，与你无关；你只评过程质量。"
)


def _litellm_completion(model: str, messages: list[dict[str, Any]], timeout: int) -> Any:
    """测试接缝：单测 monkeypatch 此函数即可全离线（D7）。"""
    import litellm  # 惰性：离线路径不加载

    litellm.suppress_debug_info = True
    return litellm.completion(model=model, messages=messages, temperature=0, timeout=timeout)


def build_digest(task_id: str, title: str, instruction: str, status: str,
                 trajectory: list[dict[str, Any]]) -> str:
    """轨迹的确定性摘要：工具名 / ok / 错误码 / 关键参数 / 最终 report 文本。"""
    lines = [f"任务 {task_id}：{title}", f"用户指令：{instruction.strip()}", f"终局状态：{status}", "轨迹："]
    for inv in trajectory:
        arguments = inv.get("arguments") or {}
        compact = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        mark = "ok" if inv.get("ok") else f"err={inv.get('error_code')}"
        lines.append(f"- {inv.get('tool')} {mark} args={compact}")
    return "\n".join(lines)


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise JudgeError(f"judge 输出不是 JSON: {content[:200]!r}") from None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise JudgeError(f"judge 输出 JSON 解析失败: {content[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise JudgeError(f"judge 输出不是 JSON object: {content[:200]!r}")
    return parsed


def run_judge(rubric: str, digest: str, judge_model: str, timeout: int = 60) -> JudgeVerdict:
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"{digest}\n\n评估 rubric：\n{rubric.strip()}"},
    ]
    try:
        response = _litellm_completion(judge_model, messages, timeout)
        content = response.choices[0].message.content or ""
    except Exception as exc:
        raise JudgeError(f"judge 调用失败: {exc}") from exc
    try:
        return JudgeVerdict.model_validate(_extract_json(content))
    except ValidationError as exc:
        raise JudgeError(f"judge 输出不合 JudgeVerdict: {exc}") from exc
