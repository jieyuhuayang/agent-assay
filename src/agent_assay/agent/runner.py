"""Episode 循环（FP05）。终止条件见 D8：report / max_steps / 单步超时 / infra_error。"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from ..env.base import BaseEnv
from ..results import Fingerprint, ResultRecord
from ..secrets import redact
from ..tasks.schema import AnyMandate, MandateSpec, TaskSpec
from ..tools import registry
from ..tools.registry import ToolContext, ToolInvocation
from .prompt import assemble_system_prompt
from .providers import ModelResponse, Provider, ProviderError, ToolCallRequest
from .user_sim import UserSimulator

_NUDGE = "请通过工具继续执行；如已完成或无法继续，请调用 report 结束。"
_MAX_PROVIDER_RETRIES = 3  # D8：重试 3 次仍失败 → infra_error
# 指数退避（秒）：真实模型跑分时限流（429/RPM 窗口）是常态，立即连打会把整个
# run 烧成 infra_error（M3 跑分修复：Gemini 曾 34/36 全灭）。10+50s 跨过分钟窗口。
_RETRY_BACKOFF_SECONDS = (10, 50)


def tool_schemas_for_llm(profile: str = "exchange") -> list[dict[str, Any]]:
    """registry → OpenAI function-calling 形态（单一事实源，R7）。

    缺省 exchange：既有调用方与 v0.1 输出字节不变（R4 承重，specs/00 M4 D-n）。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.json_schema(),
            },
        }
        for tool in registry.all_tools(profile)
    ]


def run_episode(
    task: TaskSpec,
    env: BaseEnv,
    mandate: "AnyMandate",
    provider: Provider,
    *,
    fingerprint: Fingerprint,
    step_timeout: float = 60,
) -> ResultRecord:
    user_sim = UserSimulator(task.user_script)
    ctx = ToolContext(
        env=env,
        ask_user=user_sim.ask_user,
        request_confirmation=user_sim.request_confirmation,
    )
    # 工具集按 mandate.kind 分派（D-o：kind 是唯一运行时分派源）
    tools = tool_schemas_for_llm(getattr(mandate, "kind", "exchange"))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": assemble_system_prompt(mandate)},
        {"role": "user", "content": task.instruction},
    ]

    trajectory: list[dict[str, Any]] = []
    step_timing: list[dict[str, int]] = []
    status: str = "max_steps"
    episode_start = time.monotonic()
    tokens = {"prompt_tokens": 0, "completion_tokens": 0}
    usage_seen = False

    for step in range(1, task.max_steps + 1):
        step_start = time.monotonic()
        response = _complete_with_retry(provider, messages, tools)
        if response is None:
            status = "infra_error"
            break
        if response.usage is not None:
            usage_seen = True
            tokens["prompt_tokens"] += response.usage.get("prompt_tokens", 0)
            tokens["completion_tokens"] += response.usage.get("completion_tokens", 0)

        messages.append(_assistant_message(response))

        if not response.tool_calls:
            step_timing.append(_step_ms(step, step_start))
            if time.monotonic() - step_start > step_timeout:
                status = "timeout"
                break
            messages.append({"role": "user", "content": _NUDGE})
            continue

        terminal = False
        for call in response.tool_calls:
            invocation = _execute_call(call, ctx)
            trajectory.append(
                {"step": step, "call_id": call.id, **invocation.model_dump(mode="json")}
            )
            messages.append(_tool_message(call, invocation))
            if invocation.ok and registry.get_tool(call.name) is not None:
                if registry.get_tool(call.name).category == "terminal":
                    status = invocation.arguments.get("status", "done")
                    terminal = True
                    break

        step_timing.append(_step_ms(step, step_start))
        if terminal:
            break
        if time.monotonic() - step_start > step_timeout:
            status = "timeout"
            break

    return ResultRecord(
        task_id=task.id,
        status=status,  # type: ignore[arg-type]
        fingerprint=fingerprint,
        trajectory=trajectory,
        final_state=env.export_state(),
        scoring=None,  # M1 过渡形态（Q1(a)）；FP08 起由评分流水线填充
        transcript=messages,
        timing={
            "wall_ms": int((time.monotonic() - episode_start) * 1000),
            "steps": step_timing,
            "tokens": tokens if usage_seen else None,  # timing 属 Q5 易变白名单
        },
    )


def _complete_with_retry(
    provider: Provider, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> ModelResponse | None:
    for attempt in range(_MAX_PROVIDER_RETRIES):
        try:
            return provider.complete(messages, tools)
        except ProviderError as exc:
            # 失败原因必须可见（R2：过 redact 再落日志面）；退避后重试
            print(
                f"[provider retry {attempt + 1}/{_MAX_PROVIDER_RETRIES}] {redact(str(exc))}",
                file=sys.stderr,
                flush=True,
            )
            if attempt < len(_RETRY_BACKOFF_SECONDS):
                time.sleep(_RETRY_BACKOFF_SECONDS[attempt])
    return None


def _execute_call(call: ToolCallRequest, ctx: ToolContext) -> ToolInvocation:
    if call.arguments is None:  # 工具参数不是合法 JSON（provider 保留原文）
        return ToolInvocation(
            tool=call.name,
            arguments={"_raw": call.arguments_raw or ""},
            ok=False,
            error_code="SCHEMA_VALIDATION",
            error_kind="schema_error",
            error_message="tool arguments are not valid JSON",
        )
    return registry.execute_tool(call.name, call.arguments, ctx)


def _assistant_message(response: ModelResponse) -> dict[str, Any]:
    if not response.tool_calls:
        # 无 tool_calls 时 content 必须是字符串（None 会被 OpenAI 协议 400）
        return {"role": "assistant", "content": response.text or ""}
    message: dict[str, Any] = {"role": "assistant", "content": response.text}
    message["tool_calls"] = [
        {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                # 非法参数原文包成合法 JSON object（裸串双重编码会被部分 provider 400），
                # 形态与 _execute_call 记录的 {"_raw": ...} 对齐
                "arguments": json.dumps(
                    call.arguments
                    if call.arguments is not None
                    else {"_raw": call.arguments_raw or ""},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        }
        for call in response.tool_calls
    ]
    return message


def _tool_message(call: ToolCallRequest, invocation: ToolInvocation) -> dict[str, Any]:
    if invocation.ok:
        payload: Any = invocation.result
    else:
        payload = {
            "error": {
                "code": invocation.error_code,
                "kind": invocation.error_kind,
                "message": invocation.error_message,
            }
        }
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


def _step_ms(step: int, start: float) -> dict[str, int]:
    return {"step": step, "ms": int((time.monotonic() - start) * 1000)}
