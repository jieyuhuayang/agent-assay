"""AC-08f（+ AC-08a 辅助）：judge 解析、失败降级与流水线集成。全离线（D7）。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from open_harness.results import Fingerprint, ResultRecord
from open_harness.scoring import judge as judge_mod
from open_harness.scoring.judge import JudgeError, JudgeVerdict, run_judge
from open_harness.scoring.model import ScoringContext
from open_harness.scoring.pipeline import score_episode
from open_harness.tasks.loader import load_mandate
from open_harness.tasks.schema import AssertionSpec, ExpectedSpec, TaskSpec
from open_harness.tools.registry import ToolInvocation

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_ctx():
    return ScoringContext(mandate=load_mandate(REPO_ROOT / "mandates" / "std_conservative.yaml"))


def make_task(trajectory_asserts):
    return TaskSpec(
        id="A01", family="a", title="t", instruction="i", env="mock",
        fixture="fixtures/std_account_1.yaml", mandate="mandates/std_conservative.yaml",
        expected=ExpectedSpec(
            trajectory=[AssertionSpec.model_validate({"assert": k, **p})
                        for k, p in trajectory_asserts],
            judge_rubric="报告是否清楚说明了理由？",
        ),
    )


def make_record(trajectory):
    return ResultRecord(
        task_id="A01", status="done",
        fingerprint=Fingerprint(model="scripted", model_version="scripted-v0",
                                taskset_version="v0.1.0", git_commit="deadbeef",
                                timestamp="2026-07-23T00:00:00Z", temperature="0"),
        trajectory=trajectory, final_state={"balances": {}, "open_orders": [],
                                            "new_trades": [], "new_transfers": []},
    )


REPORT_INV = ToolInvocation(
    tool="report", arguments={"text": "完成", "status": "done"}, ok=True, result={}
).model_dump(mode="json")


def fake_completion(content):
    def _fake(model, messages, timeout):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    return _fake


def test_run_judge_parses_plain_and_fenced_json(monkeypatch):
    monkeypatch.setattr(judge_mod, "_litellm_completion",
                        fake_completion('{"quality": 2, "rationale": "清楚"}'))
    verdict = run_judge("rubric", "digest", "fake-judge")
    assert verdict == JudgeVerdict(quality=2, rationale="清楚")

    fenced = '```json\n{"quality": 1, "rationale": "一般"}\n```'
    monkeypatch.setattr(judge_mod, "_litellm_completion", fake_completion(fenced))
    assert run_judge("rubric", "digest", "fake-judge").quality == 1


def test_run_judge_rejects_out_of_range_and_garbage(monkeypatch):
    monkeypatch.setattr(judge_mod, "_litellm_completion",
                        fake_completion('{"quality": 5, "rationale": "越界"}'))
    with pytest.raises(JudgeError):
        run_judge("rubric", "digest", "fake-judge")
    monkeypatch.setattr(judge_mod, "_litellm_completion", fake_completion("这不是 JSON"))
    with pytest.raises(JudgeError):
        run_judge("rubric", "digest", "fake-judge")


def test_judge_failure_degrades_gracefully(monkeypatch):
    """AC-08f：judge 挂掉 → 跳过 judge、断言分保留，episode 评分不崩。"""

    def boom(model, messages, timeout):
        raise RuntimeError("network down")

    monkeypatch.setattr(judge_mod, "_litellm_completion", boom)
    task = make_task([("tool_called", {"tool": "report"})])
    scoring = score_episode(task, make_record([REPORT_INV]), make_ctx(),
                            judge_model="fake-judge")
    assert scoring["passed"] is True          # 断言分保留
    assert scoring["judge"] is None
    assert "network down" in scoring["judge_error"]


def test_judge_skipped_without_model_or_rubric():
    task = make_task([("tool_called", {"tool": "report"})])
    scoring = score_episode(task, make_record([REPORT_INV]), make_ctx())  # 无 judge_model
    assert scoring["judge"] is None and scoring["judge_error"] is None
    assert scoring["judge_model"] is None
