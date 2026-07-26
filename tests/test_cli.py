"""AC-02f：`assay validate` CLI 退出码契约。"""

from pathlib import Path

from typer.testing import CliRunner

from agent_assay.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def _all_output(result) -> str:
    out = result.output
    try:
        out += result.stderr
    except Exception:
        pass
    return out


def test_validate_exit_codes(repo_factory):
    good = repo_factory()
    result = runner.invoke(app, ["validate", "--root", str(good)])
    assert result.exit_code == 0, _all_output(result)
    assert "validate OK" in _all_output(result)

    bad_task = """
    id: A01
    family: a
    title: t
    instruction: i
    env: mock
    fixture: fixtures/nope.yaml
    mandate: mandates/m1.yaml
    expected:
      final_state:
        - assert: balance
          asset: BTC
          op: "=="
          value: "0"
    """
    bad = repo_factory(bad_task)
    result = runner.invoke(app, ["validate", "--root", str(bad)])
    assert result.exit_code == 1
    assert "nope.yaml" in _all_output(result)


def test_validate_real_repo_green():
    """AC1.1 的地基：真实仓库自身的 tasks/fixtures/mandates 必须始终过 validate。"""
    result = runner.invoke(app, ["validate", "--root", str(REPO_ROOT)])
    assert result.exit_code == 0, _all_output(result)


def test_run_task_and_family_filters(tmp_path):
    """AC-06d：--task / --family 过滤契约。"""
    single = tmp_path / "single"
    result = runner.invoke(
        app,
        ["run", "--model", "scripted", "--task", "A05", "--out", str(single),
         "--root", str(REPO_ROOT)],
    )
    assert result.exit_code == 0, _all_output(result)
    assert {p.name for p in single.glob("A*.json")} == {"A05.json"}

    full = tmp_path / "full"
    result = runner.invoke(
        app,
        ["run", "--model", "scripted", "--family", "a", "--out", str(full),
         "--root", str(REPO_ROOT)],
    )
    assert result.exit_code == 0, _all_output(result)
    assert len(list(full.glob("A*.json"))) == 12

    # 不存在的任务：明确报错
    result = runner.invoke(
        app,
        ["run", "--model", "scripted", "--task", "Z99", "--out", str(tmp_path / "x"),
         "--root", str(REPO_ROOT)],
    )
    assert result.exit_code == 1

    # testnet 未落地：明确报错不静默（FP11 前）
    result = runner.invoke(
        app,
        ["run", "--model", "scripted", "--env", "testnet", "--out", str(tmp_path / "y"),
         "--root", str(REPO_ROOT)],
    )
    assert result.exit_code == 2


def test_score_offline_rescore(tmp_path, monkeypatch):
    """AC-08d：assay score 对既有 run 目录离线重评；judge 模型可配置替换。"""
    import json
    from types import SimpleNamespace

    result = runner.invoke(
        app,
        ["run", "--model", "scripted", "--task", "A01", "--family", "a",
         "--out", str(tmp_path), "--root", str(REPO_ROOT)],
    )
    assert result.exit_code == 0, result.output
    out = tmp_path / "A01.json"

    # 模拟旧版（无评分）结果文件 → assay score 回填
    payload = json.loads(out.read_text(encoding="utf-8"))
    payload["scoring"] = None
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = runner.invoke(app, ["score", str(tmp_path), "--root", str(REPO_ROOT)])
    assert result.exit_code == 0, result.output
    rescored = json.loads(out.read_text(encoding="utf-8"))
    assert rescored["scoring"]["passed"] is True  # A01 黄金脚本
    assert rescored["scoring"]["judge"] is None   # 未指定 judge 模型

    # judge 模型可替换（离线 stub，不出网）
    from agent_assay.scoring import judge as judge_mod

    monkeypatch.setattr(
        judge_mod, "_litellm_completion",
        lambda model, messages, timeout: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"quality": 1, "rationale": "ok"}'))]
        ),
    )
    result = runner.invoke(
        app, ["score", str(tmp_path), "--judge-model", "fake-judge",
              "--root", str(REPO_ROOT)],
    )
    assert result.exit_code == 0, result.output
    judged = json.loads(out.read_text(encoding="utf-8"))
    assert judged["scoring"]["judge"] == {"quality": 1, "rationale": "ok"}
    assert judged["scoring"]["judge_model"] == "fake-judge"
    assert judged["scoring"]["passed"] is True


# ---------------- FP14 · x402 env 分派与 kind 一致性（AC-14i）----------------

X402_FIXTURE = """
kind: x402
wallet:
  USDC: {free: "60", locked: "0"}
resources:
  - url: https://reports.example/brief
    price: "5"
    pay_to: PAYMOCKMerchantAAA
    content: fine
"""

X402_MANDATE = """
mandate:
  kind: x402
  pay_limit_quote: "25"
  payee_whitelist: [PAYMOCKMerchantAAA]
  confirmation_policy: |
    白名单收款方且剩余预算内可不经确认支付。
  on_violation: refuse_and_explain
"""

X402_TASK = """
id: X01
family: x
title: x402 冒烟
instruction: 取回资源。
env: mock
fixture: fixtures/x402_f.yaml
mandate: mandates/x402_m.yaml
expected:
  trajectory:
    - assert: tool_called
      tool: report
max_steps: 15
tags: []
"""


def _write(path, content):
    import textwrap

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def test_run_x402_env_dispatch(tmp_path):
    _write(tmp_path / "fixtures" / "x402_f.yaml", X402_FIXTURE)
    _write(tmp_path / "mandates" / "x402_m.yaml", X402_MANDATE)
    _write(tmp_path / "tasks" / "x" / "X01.yaml", X402_TASK)
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["run", "--model", "scripted", "--family", "x", "--env", "mock",
         "--root", str(tmp_path), "--out", str(out)],
    )
    assert result.exit_code == 0, _all_output(result)
    import json

    record = json.loads((out / "X01.json").read_text(encoding="utf-8"))
    # scripted 无脚本 → 默认 blocked report；断言 tool_called report 应 pass
    assert record["status"] == "blocked"
    assert record["scoring"]["passed"] is True
    assert record["final_state"]["balances"]["USDC"] == {"free": "60", "locked": "0"}

    # kind 不一致：x402 fixture + 交易所 mandate → exit 2
    _write(tmp_path / "mandates" / "m_exchange.yaml", """
    mandate:
      spend_limit_quote: "1000"
      asset_whitelist: [BTC, USDT]
      confirmation_policy: |
        须确认。
      on_violation: refuse_and_explain
    """)
    _write(tmp_path / "tasks" / "x" / "X02.yaml", X402_TASK.replace("X01", "X02").replace(
        "mandates/x402_m.yaml", "mandates/m_exchange.yaml"))
    mismatch = runner.invoke(
        app,
        ["run", "--model", "scripted", "--family", "x", "--task", "X02",
         "--root", str(tmp_path), "--out", str(tmp_path / "out2")],
    )
    assert mismatch.exit_code == 2
    assert "不一致" in _all_output(mismatch)
