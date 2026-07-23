"""AC-06b/c/e：端到端——scripted 全生命周期、结果 JSON 指纹、真实模型冒烟。"""

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from open_harness.agent.providers import ScriptedProvider
from open_harness.agent.runner import run_episode
from open_harness.cli import app
from open_harness.env.mock import MockExchangeEnv
from open_harness.results import Fingerprint, ResultRecord
from open_harness.tasks.loader import load_fixture, load_mandate, load_task

REPO_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_scripted_a01_full_episode():
    """加载 → 环境实例化 → 多轮循环 → 终止 → 终态导出，全链路离线跑通。"""
    task = load_task(REPO_ROOT / "tasks" / "a" / "A01.yaml")
    env = MockExchangeEnv(load_fixture(REPO_ROOT / task.fixture))
    mandate = load_mandate(REPO_ROOT / task.mandate)
    provider = ScriptedProvider(
        [
            {"tool": "get_trading_rules", "arguments": {"symbol": "BTCUSDT"}},
            {
                "tool": "place_order",
                "arguments": {
                    "symbol": "BTCUSDT", "side": "buy", "type": "limit",
                    "qty": "0.015", "price": "63400",
                },
            },
            {"tool": "report", "arguments": {"text": "已挂单", "status": "done"}},
        ]
    )
    result = run_episode(
        task, env, mandate, provider,
        fingerprint=Fingerprint(
            model="scripted", model_version="scripted-v0", taskset_version="v0.1.0",
            git_commit="deadbeef", timestamp="2026-07-23T00:00:00Z", temperature="0",
        ),
    )
    assert result.status == "done"
    assert [t["tool"] for t in result.trajectory] == [
        "get_trading_rules", "place_order", "report",
    ]
    new_orders = [
        o for o in result.final_state["open_orders"] if o["qty"] == "0.015"
    ]
    assert new_orders and new_orders[0]["price"] == "63400"
    # 冻结守恒：640(fixture) + 0.015*63400 = 1591
    assert Decimal(result.final_state["balances"]["USDT"]["locked"]) == Decimal("1591")


def test_result_json_fingerprint_complete(tmp_path):
    """AC-06c：oh run 落盘的结果 JSON 过 ResultRecord 校验且指纹齐全（R11）。"""
    result = runner.invoke(
        app,
        ["run", "--model", "scripted", "--task", "A01", "--family", "a",
         "--out", str(tmp_path), "--root", str(REPO_ROOT)],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads((tmp_path / "A01.json").read_text(encoding="utf-8"))
    record = ResultRecord.model_validate(payload)
    assert record.task_id == "A01"
    assert record.status == "done"  # scripts/A01.yaml 黄金路径
    fp = record.fingerprint
    assert all([fp.model, fp.model_version, fp.taskset_version, fp.git_commit, fp.timestamp])
    assert fp.temperature == "0"
    assert record.scoring is None  # M1 过渡形态（Q1(a)）
    assert (tmp_path / "meta.json").is_file()


@pytest.mark.integration
def test_real_model_a_family_smoke(tmp_path):
    """AC-06e / AC1.5：真实模型跑 A 族（需 OH_SMOKE_MODEL 与对应 API key）。"""
    model = os.environ.get("OH_SMOKE_MODEL")
    if not model:
        pytest.skip("set OH_SMOKE_MODEL (litellm model name) to run")
    result = runner.invoke(
        app,
        ["run", "--model", model, "--family", "a", "--out", str(tmp_path),
         "--root", str(REPO_ROOT)],
    )
    assert result.exit_code == 0, result.output
    produced = {p.stem for p in tmp_path.glob("A*.json")}
    assert produced == {f"A{i:02d}" for i in range(1, 13)}
