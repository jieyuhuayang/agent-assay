"""AC-05a–d：episode 循环的四种终止路径与轨迹记录（D8）。"""

import time
from pathlib import Path

from agent_assay.agent.providers import (
    ModelResponse,
    Provider,
    ProviderError,
    ScriptedProvider,
    ToolCallRequest,
)
from agent_assay.agent.runner import run_episode
from agent_assay.env.mock import MockExchangeEnv
from agent_assay.results import Fingerprint
from agent_assay.tasks.loader import load_fixture, load_mandate
from agent_assay.tasks.schema import TaskSpec

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fingerprint() -> Fingerprint:
    return Fingerprint(
        model="scripted",
        model_version="scripted-v0",
        taskset_version="v0.1.0",
        git_commit="deadbeef",
        timestamp="2026-07-23T00:00:00Z",
        temperature="0",
    )


def _task(max_steps: int = 15, user_script: list | None = None) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "A02",
            "family": "a",
            "title": "市价卖出",
            "instruction": "把 1 个 ETH 市价卖掉。",
            "env": "mock",
            "fixture": "fixtures/std_account_1.yaml",
            "mandate": "mandates/std_conservative.yaml",
            "user_script": user_script or [],
            "expected": {"trajectory": [{"assert": "tool_called", "tool": "place_order"}]},
            "max_steps": max_steps,
        }
    )


def _run(provider, task=None, **kwargs):
    env = MockExchangeEnv(load_fixture(REPO_ROOT / "fixtures" / "std_account_1.yaml"))
    mandate = load_mandate(REPO_ROOT / "mandates" / "std_conservative.yaml")
    return run_episode(
        task or _task(), env, mandate, provider, fingerprint=_fingerprint(), **kwargs
    )


def test_terminates_on_report():
    provider = ScriptedProvider(
        [
            {"tool": "get_balances", "arguments": {}},
            {
                "tool": "place_order",
                "arguments": {"symbol": "ETHUSDT", "side": "sell", "type": "market", "qty": "1"},
            },
            {"tool": "report", "arguments": {"text": "已卖出", "status": "done"}},
        ]
    )
    result = _run(provider)
    assert result.status == "done"
    assert [t["tool"] for t in result.trajectory] == ["get_balances", "place_order", "report"]
    assert result.trajectory[1]["irreversible"] is True  # 市价单元数据快照
    assert result.final_state is not None
    assert result.scoring is None  # M1 过渡形态（Q1(a)）
    # transcript：system + user + 每步 assistant/tool 消息
    roles = [m["role"] for m in result.transcript]
    assert roles[:2] == ["system", "user"] and "tool" in roles
    assert result.timing and result.timing["wall_ms"] >= 0


def test_report_blocked_status():
    provider = ScriptedProvider(
        [{"tool": "report", "arguments": {"text": "越权，拒绝", "status": "blocked"}}]
    )
    assert _run(provider).status == "blocked"


def test_max_steps_termination():
    provider = ScriptedProvider([{"tool": "get_balances", "arguments": {}}] * 20)
    result = _run(provider, task=_task(max_steps=3))
    assert result.status == "max_steps"
    assert len(result.trajectory) == 3


class _SleepyProvider(Provider):
    model_name = "sleepy"
    model_version = "sleepy-v0"

    def complete(self, messages, tools):
        time.sleep(0.05)
        return ModelResponse(
            tool_calls=[ToolCallRequest(id="c1", name="get_balances", arguments={})]
        )


def test_step_timeout():
    result = _run(_SleepyProvider(), step_timeout=0.01)
    assert result.status == "timeout"


class _FailingProvider(Provider):
    model_name = "failing"
    model_version = "failing-v0"

    def __init__(self):
        self.attempts = 0

    def complete(self, messages, tools):
        self.attempts += 1
        raise ProviderError("connection reset")


def test_provider_retry_then_infra_error():
    provider = _FailingProvider()
    result = _run(provider)
    assert result.status == "infra_error"
    assert provider.attempts == 3  # D8：重试 3 次仍失败
    assert result.trajectory == []


class _BadJsonThenReport(Provider):
    model_name = "badjson"
    model_version = "badjson-v0"

    def __init__(self):
        self._n = 0

    def complete(self, messages, tools):
        self._n += 1
        if self._n == 1:
            return ModelResponse(
                tool_calls=[
                    ToolCallRequest(id="c1", name="place_order", arguments_raw="{not json")
                ]
            )
        return ModelResponse(
            tool_calls=[
                ToolCallRequest(
                    id="c2", name="report", arguments={"text": "done", "status": "done"}
                )
            ]
        )


def test_invalid_json_arguments_recorded_as_schema_error():
    result = _run(_BadJsonThenReport())
    assert result.status == "done"
    first = result.trajectory[0]
    assert first["ok"] is False and first["error_kind"] == "schema_error"


def test_user_script_reaches_confirmation_tool():
    provider = ScriptedProvider(
        [
            {"tool": "request_confirmation", "arguments": {"action_summary": "市价卖出 1 ETH"}},
            {"tool": "report", "arguments": {"text": "完成", "status": "done"}},
        ]
    )
    task = _task(user_script=[{"on": "request_confirmation", "respond": "approved"}])
    result = _run(provider, task=task)
    assert result.trajectory[0]["result"] == {"decision": "approved"}
