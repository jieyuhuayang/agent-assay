"""AC-05f：scripted provider 零依赖离线回放；litellm 惰性加载。"""

import sys

from agent_assay.agent.providers import ScriptedProvider


def test_scripted_provider_offline():
    sys.modules.pop("litellm", None)

    provider = ScriptedProvider(
        [
            {"tool": "get_balances", "arguments": {}},
            {"tool": "report", "arguments": {"text": "done", "status": "done"}, "text": "收尾"},
        ]
    )
    first = provider.complete([], [])
    assert first.tool_calls[0].name == "get_balances"
    assert first.tool_calls[0].arguments == {}

    second = provider.complete([], [])
    assert second.text == "收尾"
    assert second.tool_calls[0].name == "report"

    # 脚本耗尽：纯文本，无工具调用
    third = provider.complete([], [])
    assert third.tool_calls == [] and "exhausted" in third.text

    assert provider.model_name == "scripted" and provider.model_version == "scripted-v0"
    # 全程未加载任何网络栈（D7：离线、无 key 可跑）
    assert "litellm" not in sys.modules
