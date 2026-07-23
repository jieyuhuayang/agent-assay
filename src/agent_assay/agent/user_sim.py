"""脚本化用户模拟器（D5）：确定性回复，不用 LLM。

规则按事件类型取首条未消耗者并消耗；不同类型的规则互不阻塞，
使 agent 的调用顺序与脚本书写顺序解耦（仍完全确定）。
耗尽后返回 NO_RESPONSE，episode 继续或由 agent 收尾。
"""

from __future__ import annotations

from ..tasks.schema import UserScriptRule

NO_RESPONSE = "用户无回应"


class UserSimulator:
    def __init__(self, script: list[UserScriptRule]) -> None:
        self._script: list[UserScriptRule] = list(script)

    def ask_user(self, question: str) -> str:
        return self._consume("ask_user")

    def request_confirmation(self, action_summary: str) -> str:
        return self._consume("request_confirmation")

    def _consume(self, event: str) -> str:
        for i, rule in enumerate(self._script):
            if rule.on == event:
                del self._script[i]
                return rule.respond
        return NO_RESPONSE
