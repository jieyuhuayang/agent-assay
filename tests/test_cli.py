"""AC-02f：`oh validate` CLI 退出码契约。"""

from pathlib import Path

from typer.testing import CliRunner

from open_harness.cli import app

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
    """AC-08d：oh score 对既有 run 目录离线重评；judge 模型可配置替换。"""
    import json
    from types import SimpleNamespace

    result = runner.invoke(
        app,
        ["run", "--model", "scripted", "--task", "A01", "--family", "a",
         "--out", str(tmp_path), "--root", str(REPO_ROOT)],
    )
    assert result.exit_code == 0, result.output
    out = tmp_path / "A01.json"

    # 模拟旧版（无评分）结果文件 → oh score 回填
    payload = json.loads(out.read_text(encoding="utf-8"))
    payload["scoring"] = None
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = runner.invoke(app, ["score", str(tmp_path), "--root", str(REPO_ROOT)])
    assert result.exit_code == 0, result.output
    rescored = json.loads(out.read_text(encoding="utf-8"))
    assert rescored["scoring"]["passed"] is True  # A01 黄金脚本
    assert rescored["scoring"]["judge"] is None   # 未指定 judge 模型

    # judge 模型可替换（离线 stub，不出网）
    from open_harness.scoring import judge as judge_mod

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
