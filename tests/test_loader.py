"""AC-02a：YAML 加载往返无损，数字为 Decimal。"""

from decimal import Decimal

from open_harness.tasks.loader import load_fixture, load_mandate, load_task


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
