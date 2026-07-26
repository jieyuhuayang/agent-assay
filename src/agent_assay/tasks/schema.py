"""任务 / 授权书 schema（KICKOFF 7.1 / 6 节）。

断言类型全集（7.2 节）在此定义为常量，FP02 `assay validate` 据此拒绝未知类型，
FP07 断言引擎按此实现；两处共用一份清单，防止散落。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..money import Money

FINAL_STATE_ASSERTIONS = frozenset(
    {
        "balance",
        "open_order_exists",
        "open_order_absent",
        "order_state",
        "no_new_trades",
        "spend_within",
    }
)

TRAJECTORY_ASSERTIONS = frozenset(
    {
        "confirm_before_irreversible",
        "tool_called",
        "tool_not_called",
        "clarify_before_action",
        "call_order",
        "no_forbidden_address",
        "report_answer",
        "max_semantic_errors",
    }
)


class AssertionSpec(BaseModel):
    """单条断言。YAML 键 `assert` 是 Python 关键字 → 字段名 kind + alias。

    各断言类型的附加参数（asset/op/value/tool/where/…）随类型而异，
    先以 extra 字段透传，FP07 按类型收紧校验。
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    kind: str = Field(alias="assert")


class UserScriptRule(BaseModel):
    """脚本化用户回复（D5）：按序消耗，耗尽后返回「用户无回应」。"""

    on: Literal["ask_user", "request_confirmation"]
    respond: str


class ExpectedSpec(BaseModel):
    final_state: list[AssertionSpec] = []
    trajectory: list[AssertionSpec] = []
    judge_rubric: str | None = None


class TaskSpec(BaseModel):
    id: str = Field(pattern=r"^[ABCX]\d{2}$")
    family: Literal["a", "b", "c", "x"]
    title: str
    instruction: str
    env: Literal["mock", "testnet", "both"]
    fixture: str
    mandate: str
    user_script: list[UserScriptRule] = []
    expected: ExpectedSpec
    max_steps: int = 15
    tags: list[str] = []


class MandateSpec(BaseModel):
    """授权书（第 6 节）。spend_limit_quote 口径：仅计买入方向 quote 流出（Q3 定案）。

    kind 是 v0.2 判别字段（specs/00 M4 D-o）：旧 YAML 无此键 → 默认 exchange，零文件翻新；
    运行时分派（prompt 模板 / 工具 profile / 评分分支）一律以 mandate.kind 为唯一事实源。
    """

    kind: Literal["exchange"] = "exchange"
    spend_limit_quote: Money
    asset_whitelist: list[str]
    withdraw_whitelist: dict[str, list[str]] = {}
    confirmation_policy: str
    on_violation: Literal["refuse_and_explain"]


class X402MandateSpec(BaseModel):
    """x402 支付授权书（specs/13）。pay_limit_quote：单任务累计支付上限（仅计支出）。"""

    kind: Literal["x402"]
    pay_limit_quote: Money
    payee_whitelist: list[str]
    confirmation_policy: str
    on_violation: Literal["refuse_and_explain"]


AnyMandate = MandateSpec | X402MandateSpec
