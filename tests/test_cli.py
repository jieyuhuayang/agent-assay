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
