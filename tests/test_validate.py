"""AC-02b / AC-02e：引用完整性与未知断言类型。"""

from agent_assay.tasks.validate import validate_repo


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


def test_fixture_invariant_checked(repo_factory):
    """FP03：validate 对 fixture 追加自洽性检查（locked 与挂单守恒）。"""
    inconsistent = """
    balances:
      USDT: {free: "1000", locked: "999"}   # 无挂单却有冻结
    rules:
      BTCUSDT:
        base: BTC
        quote: USDT
        step_size: "0.00001"
        min_qty: "0.00001"
        min_notional: "5"
        tick_size: "0.01"
        price_min: "0.01"
        price_max: "1000000"
        maker_fee: "0.001"
        taker_fee: "0.001"
    tickers: {}
    """
    report = validate_repo(repo_factory(task_yaml=None, fixture_yaml=inconsistent))
    assert any(issue.code == "fixture-invariant" for issue in report.issues)


def test_x402_fixture_kind_dispatch(repo_factory, tmp_path):
    """AC-13j：kind: x402 的 fixture 走自己的校验，不进 MockExchangeEnv；坏 fixture 被标记。"""
    import textwrap

    from agent_assay.tasks.validate import validate_repo

    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")

    root = repo_factory()  # 交易所语料齐备的合法仓库
    write(
        root / "fixtures" / "x402_ok.yaml",
        """
        kind: x402
        wallet:
          USDC: {free: "60", locked: "0"}
        resources:
          - url: https://reports.example/brief
            price: "5"
            pay_to: PAYMOCKMerchantAAA
            content: fine
        """,
    )
    report = validate_repo(root)
    assert report.ok, [i.message for i in report.issues]  # x402 fixture 不被交易所校验误伤

    write(
        root / "fixtures" / "x402_bad.yaml",
        """
        kind: x402
        wallet:
          USDC: {free: "-1", locked: "0"}
        resources:
          - url: https://reports.example/brief
            price: "0"
            pay_to: PAYMOCKMerchantAAA
            content: broken
        """,
    )
    report = validate_repo(root)
    assert not report.ok
    assert any(i.code == "schema" and "x402_bad" in i.file for i in report.issues)


def _x402_repo(repo_factory):
    """在交易所合法仓库上加 x402 fixture/mandate（域测试公用底座）。"""
    import textwrap

    root = repo_factory()

    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")

    write(root / "fixtures" / "x402_f.yaml", """
    kind: x402
    wallet:
      USDC: {free: "60", locked: "0"}
    resources:
      - url: https://reports.example/brief
        price: "5"
        pay_to: PAYMOCKMerchantAAA
        content: fine
    """)
    write(root / "mandates" / "x402_m.yaml", """
    mandate:
      kind: x402
      pay_limit_quote: "25"
      payee_whitelist: [PAYMOCKMerchantAAA]
      confirmation_policy: |
        限内免确认。
      on_violation: refuse_and_explain
    """)
    return root, write


X_TASK_TEMPLATE = """
id: X01
family: x
title: t
instruction: i
env: {env}
fixture: {fixture}
mandate: {mandate}
expected:
  final_state:
{final_state}
  trajectory:
    - assert: tool_called
      tool: report
max_steps: 15
tags: []
"""


def test_domain_gated_assertion_kinds(repo_factory):
    """AC-15f：x 族禁交易所专属断言；a/b/c 族禁支付断言（issue code domain-assert）。"""
    from agent_assay.tasks.validate import validate_repo

    root, write = _x402_repo(repo_factory)
    # x 任务用 spend_within → domain-assert
    write(root / "tasks" / "x" / "X01.yaml", X_TASK_TEMPLATE.format(
        env="mock", fixture="fixtures/x402_f.yaml", mandate="mandates/x402_m.yaml",
        final_state="    - assert: spend_within\n      limit: \"10\"",
    ))
    report = validate_repo(root)
    assert any(i.code == "domain-assert" and "spend_within" in i.message for i in report.issues)

    # a 任务用 payments_within → domain-assert
    write(root / "tasks" / "x" / "X01.yaml", X_TASK_TEMPLATE.format(
        env="mock", fixture="fixtures/x402_f.yaml", mandate="mandates/x402_m.yaml",
        final_state="    - assert: payments_within",
    ))
    write(root / "tasks" / "a" / "A02.yaml", """
id: A02
family: a
title: t
instruction: i
env: mock
fixture: fixtures/f1.yaml
mandate: mandates/m1.yaml
expected:
  final_state:
    - assert: payments_within
max_steps: 15
tags: []
""")
    report = validate_repo(root)
    assert any(
        i.code == "domain-assert" and "payments_within" in i.message and "A02" in i.file
        for i in report.issues
    )


def test_family_domain_coherence(repo_factory):
    """AC-15f：family=x ⇔ fixture/mandate kind=x402 ⇒ env mock；交易所语料零扰。"""
    from agent_assay.tasks.validate import validate_repo

    root, write = _x402_repo(repo_factory)
    ok_task = X_TASK_TEMPLATE.format(
        env="mock", fixture="fixtures/x402_f.yaml", mandate="mandates/x402_m.yaml",
        final_state="    - assert: payments_within",
    )
    write(root / "tasks" / "x" / "X01.yaml", ok_task)
    report = validate_repo(root)
    assert report.ok, [i.message for i in report.issues]  # 合规 x 任务 + 既有交易所语料全绿

    # x 任务引用交易所 fixture/mandate → domain
    write(root / "tasks" / "x" / "X01.yaml", X_TASK_TEMPLATE.format(
        env="mock", fixture="fixtures/f1.yaml", mandate="mandates/m1.yaml",
        final_state="    - assert: payments_within",
    ))
    report = validate_repo(root)
    assert sum(1 for i in report.issues if i.code == "domain") == 2  # fixture + mandate 各一条

    # x 任务 env: both → domain
    write(root / "tasks" / "x" / "X01.yaml", X_TASK_TEMPLATE.format(
        env="both", fixture="fixtures/x402_f.yaml", mandate="mandates/x402_m.yaml",
        final_state="    - assert: payments_within",
    ))
    report = validate_repo(root)
    assert any(i.code == "domain" and "env" in i.message for i in report.issues)
