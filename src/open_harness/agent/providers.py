"""模型适配层（D7）：litellm 统一 function-calling + 零依赖 scripted 回放。

本模块是 src 中唯一允许 import litellm/HTTP 客户端的位置之一（specs/00 · 5.2 D-a），
且 litellm 惰性 import——离线测试（scripted）永不加载网络栈。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import deque
from typing import Any

from pydantic import BaseModel


class ProviderError(RuntimeError):
    """provider 调用失败（网络/限流/超时等）。runner 重试 3 次后记 infra_error（D8）。"""


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] | None = None
    arguments_raw: str | None = None  # JSON 解析失败时保留原文，runner 记 schema_error


class ModelResponse(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCallRequest] = []


class Provider(ABC):
    """统一接口：OpenAI 消息形态（litellm 原生）。"""

    model_name: str
    model_version: str

    @abstractmethod
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse: ...


class ScriptedProvider(Provider):
    """按预录动作序列回放（每步一个工具调用），耗尽后返回纯文本。

    动作形态：{"tool": str, "arguments": dict, "text": str|None}
    """

    def __init__(self, actions: list[dict[str, Any]]) -> None:
        self.model_name = "scripted"
        self.model_version = "scripted-v0"
        self._queue = deque(actions)
        self._counter = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        if not self._queue:
            return ModelResponse(text="(scripted provider exhausted)")
        action = self._queue.popleft()
        self._counter += 1
        return ModelResponse(
            text=action.get("text"),
            tool_calls=[
                ToolCallRequest(
                    id=f"call_{self._counter}",
                    name=action["tool"],
                    arguments=action.get("arguments", {}),
                )
            ],
        )


class LiteLLMProvider(Provider):
    """litellm 统一适配：temperature=0（D7），版本指纹取自首个响应。"""

    def __init__(self, model: str, timeout: int = 60) -> None:
        self.model_name = model
        self.model_version = model  # 首个响应后回填精确版本
        self._timeout = timeout
        import litellm  # 惰性：离线测试不加载

        litellm.suppress_debug_info = True
        self._litellm = litellm

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        try:
            response = self._litellm.completion(
                model=self.model_name,
                messages=messages,
                tools=tools,
                temperature=0,
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 —— 各家 SDK 异常类型繁杂，统一转 ProviderError
            raise ProviderError(f"litellm completion failed: {exc}") from exc

        try:
            if getattr(response, "model", None):
                self.model_version = response.model
            message = response.choices[0].message
            tool_calls: list[ToolCallRequest] = []
            for call in message.tool_calls or []:
                raw = call.function.arguments or "{}"
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    tool_calls.append(
                        ToolCallRequest(id=call.id, name=call.function.name, arguments=parsed)
                    )
                else:
                    # 非法 JSON 或非 object（数组/字符串/null）→ 原文保留，
                    # runner 记 schema_error 回喂模型自我修正
                    tool_calls.append(
                        ToolCallRequest(id=call.id, name=call.function.name, arguments_raw=raw)
                    )
            return ModelResponse(text=message.content, tool_calls=tool_calls)
        except Exception as exc:  # noqa: BLE001 —— 畸形响应与网络失败同权：交给 D8 重试/infra_error
            raise ProviderError(f"malformed litellm response: {exc}") from exc
