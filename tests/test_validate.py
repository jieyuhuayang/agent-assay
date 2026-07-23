"""AC-02b / AC-02e：引用完整性与未知断言类型。"""

from open_harness.tasks.validate import validate_repo


def _codes(report):
    return [issue.code for issue in report.issues]


def test_valid_repo_green(repo_factory):
    report = validate_repo(repo_factory())
    assert report.ok, report.issues
    assert (report.tasks, report.fixtures, report.mandates) == (1, 1, 1)


def test_missing_reference_fails(repo_factory):
    bad_task = """
    id: A01
    family: a
    title: t
    instruction: i
    env: mock
    fixture: fixtures/nope.yaml
    mandate: mandates/missing.yaml
    expected:
      final_state:
        - assert: balance
          asset: BTC
          op: "=="
          value: "0"
    """
    report = validate_repo(repo_factory(bad_task))
    refs = [issue for issue in report.issues if issue.code == "ref"]
    assert len(refs) == 2
    assert any("nope.yaml" in issue.message for issue in refs)
    assert any("missing.yaml" in issue.message for issue in refs)


def test_unknown_assertion_type_rejected(repo_factory):
    bad_task = """
    id: A01
    family: a
    title: t
    instruction: i
    env: mock
    fixture: fixtures/f1.yaml
    mandate: mandates/m1.yaml
    expected:
      final_state:
        - assert: quantum_check
        - assert: tool_called   # 轨迹类断言放进了终态区
          tool: place_order
    """
    report = validate_repo(repo_factory(bad_task))
    kinds = [issue for issue in report.issues if issue.code == "assert-kind"]
    assert len(kinds) == 2
    assert any("quantum_check" in issue.message for issue in kinds)
    assert any("放错位置" in issue.message for issue in kinds)


def test_bare_float_rejected(repo_factory):
    """R9 数据面：YAML 裸 float 必须被 validate 拦下。"""
    bad_fixture = """
    balances:
      USDT: {free: 1000.5, locked: "0"}
    rules: {}
    tickers: {}
    """
    report = validate_repo(repo_factory(fixture_yaml=bad_fixture))
    floats = [issue for issue in report.issues if issue.code == "float"]
    assert floats and "free" in floats[0].message
