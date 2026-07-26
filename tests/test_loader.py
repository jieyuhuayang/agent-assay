"""AC-02a：YAML 加载往返无损，数字为 Decimal。"""

from decimal import Decimal

from agent_assay.tasks.loader import load_fixture, load_mandate, load_task


def test_task_yaml_roundtrip(repo_factory):
    root = repo_factory()

    task = load_task(root / "tasks" / "a" / "A01.yaml")
    assert task.id == "A01"
    assert task.family == "a"
    assert task.max_steps == 15
    assert task.expected.final_state[0].kind == "open_order_exists"
    # 往返：dump 回 YAML 形态时 alias `assert` 保留，附加参数无损
    dumped = task.expected.final_state[0].model_dump(by_alias=True)
    assert dumped["assert"] == "open_order_exists"
    assert dumped["symbol"] == "BTCUSDT"

    mandate = load_mandate(root / "mandates" / "m1.yaml")
    assert mandate.spend_limit_quote == Decimal("1000")
    assert isinstance(mandate.spend_limit_quote, Decimal)

    fixture = load_fixture(root / "fixtures" / "f1.yaml")
    assert fixture.rules["BTCUSDT"].step_size == Decimal("0.00001")
    assert fixture.balances["BTC"].free == Decimal("0.5")
    assert isinstance(fixture.tickers["BTCUSDT"].last, Decimal)


def test_load_fixture_dispatches_on_kind(tmp_path):
    """AC-13a：loader 按顶层 kind 分派 schema；未知 kind 明确报错（FP13）。"""
    import pytest

    from agent_assay.env.fixture import FixtureSpec
    from agent_assay.env.x402_fixture import X402FixtureSpec

    x402 = tmp_path / "x.yaml"
    x402.write_text(
        'kind: x402\nwallet:\n  USDC: {free: "10", locked: "0"}\n'
        'resources:\n  - url: https://a.example/r\n    price: "1"\n'
        "    pay_to: PAYMOCKMerchantAAA\n    content: c\n",
        encoding="utf-8",
    )
    assert isinstance(load_fixture(x402), X402FixtureSpec)

    unknown = tmp_path / "u.yaml"
    unknown.write_text("kind: martian\nfoo: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kind"):
        load_fixture(unknown)

    legacy = tmp_path / "legacy.yaml"
    legacy.write_text(
        'balances:\n  USDT: {free: "1", locked: "0"}\nrules: {}\ntickers: {}\n',
        encoding="utf-8",
    )
    assert isinstance(load_fixture(legacy), FixtureSpec)  # 无 kind → 交易所缺省
