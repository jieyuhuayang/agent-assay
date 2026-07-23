"""FP11 · Testnet 环境单元测试（AC-11b/11c/11d + specs/11 D-j/D-k 补充项）。

全部离线：ccxt client 以 stub 注入（specs/11 §3——client 注入仅供测试）。
AC-11a（R1 剪枝）在 tests/test_redlines.py::test_r1_testnet_client_uses_whitelisted_base。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import ccxt
import pytest

from agent_assay.env.base import ExchangeError
from agent_assay.env import testnet as testnet_env


# ---------------------------------------------------- AC-11b：key 纪律 ----


def test_keys_only_from_env(monkeypatch):
    monkeypatch.delenv("OH_TESTNET_API_KEY", raising=False)
    monkeypatch.delenv("OH_TESTNET_API_SECRET", raising=False)
    with pytest.raises(testnet_env.TestnetConfigError) as exc_info:
        testnet_env.TestnetExchangeEnv()
    message = str(exc_info.value)
    assert "OH_TESTNET_API_KEY" in message and "OH_TESTNET_API_SECRET" in message

    # 只给 key 不给 secret 同样失败，且消息仍点名两个变量
    monkeypatch.setenv("OH_TESTNET_API_KEY", "half-configured-key")
    with pytest.raises(testnet_env.TestnetConfigError) as exc_info:
        testnet_env.TestnetExchangeEnv()
    assert "OH_TESTNET_API_SECRET" in str(exc_info.value)


# ------------------------------------------------- AC-11c：withdraw 模拟 ----


class _ExplosiveClient:
    """任何属性访问即炸——证明 withdraw 全程不触碰 ccxt client。"""

    def __getattr__(self, name):
        raise AssertionError(f"withdraw 不得触碰 ccxt client（访问了 {name}）")


def test_withdraw_simulated():
    env = testnet_env.TestnetExchangeEnv(client=_ExplosiveClient())
    receipt = env.withdraw("USDT", Decimal("10"), "TMockWhitelistedAddressAAA", "TRC20")
    assert receipt.simulated is True
    assert receipt.transfer_id.startswith("SIM-")
    assert receipt.amount == Decimal("10")
    # 连续提币回执 id 递增，互不相同
    second = env.withdraw("USDT", Decimal("1"), "TMockWhitelistedAddressAAA", "TRC20")
    assert second.transfer_id != receipt.transfer_id


# ------------------------------------------------- AC-11d：网络降级 ----


class _DownClient:
    def fetch_balance(self):
        raise ccxt.NetworkError("connection reset by peer")

    def fetch_time(self):
        raise ccxt.RequestTimeout("timed out")  # NetworkError 子类


def test_network_failure_graceful_degradation():
    env = testnet_env.TestnetExchangeEnv(client=_DownClient())
    with pytest.raises(testnet_env.TestnetUnavailableError) as exc_info:
        env.get_balances()
    assert "mock" in str(exc_info.value)  # 明确提示改用 mock，不静默跳过
    with pytest.raises(testnet_env.TestnetUnavailableError):
        env.ping()


# ------------------------------------------------- D-j：错误映射表 ----


@pytest.mark.parametrize(
    "raised, expected_code",
    [
        (ccxt.BadSymbol("bad"), "INVALID_SYMBOL"),
        (ccxt.InsufficientFunds("poor"), "INSUFFICIENT_BALANCE"),
        (ccxt.OrderNotFound("gone"), "UNKNOWN_ORDER"),
        (ccxt.InvalidOrder("weird"), "INVALID_ORDER"),
        (ccxt.ExchangeError("misc"), "EXCHANGE_ERROR"),
    ],
)
def test_ccxt_error_mapping(raised, expected_code):
    class _Raising:
        def fetch_balance(self):
            raise raised

    env = testnet_env.TestnetExchangeEnv(client=_Raising())
    with pytest.raises(ExchangeError) as exc_info:
        env.get_balances()
    assert exc_info.value.code == expected_code


# ------------------------------------------------- 映射与裁剪细节 ----


def test_get_balances_maps_and_filters_zero():
    class _Balances:
        def fetch_balance(self):
            return {
                "free": {"BTC": 0.5, "USDT": 1000.0, "DUST": 0.0},
                "used": {"BTC": 0.1, "USDT": 0, "DUST": 0},
            }

    env = testnet_env.TestnetExchangeEnv(client=_Balances())
    balances = env.get_balances()
    assert balances["BTC"].free == Decimal("0.5") and balances["BTC"].locked == Decimal("0.1")
    assert "DUST" not in balances  # 全零资产不进账面


def test_transfer_history_unsupported_is_honest():
    env = testnet_env.TestnetExchangeEnv(client=_ExplosiveClient())
    with pytest.raises(ExchangeError) as exc_info:
        env.get_transfer_history("deposit")
    assert exc_info.value.code == "UNSUPPORTED"


def test_stop_limit_unsupported():
    env = testnet_env.TestnetExchangeEnv(client=_ExplosiveClient())
    with pytest.raises(ExchangeError) as exc_info:
        env.place_order("BTCUSDT", "buy", "stop_limit", qty=Decimal("1"),
                        price=Decimal("1"), stop_price=Decimal("1"))
    assert exc_info.value.code == "UNSUPPORTED"


# ------------------------------------------------- D-k：结构评分模式 ----


def test_structural_scoring_mode():
    from agent_assay.scoring.pipeline import score_episode_structural

    def record(status, trajectory):
        return SimpleNamespace(status=status, trajectory=trajectory)

    ok_inv = {"tool": "get_balances", "ok": True, "step": 1}
    schema_bad = {"tool": "place_order", "ok": False, "error_kind": "schema_error", "step": 2}
    semantic_bad = {"tool": "place_order", "ok": False, "error_kind": "semantic_error", "step": 2}

    healthy = score_episode_structural(record("done", [ok_inv, semantic_bad]))
    assert healthy["mode"] == "structural"
    assert healthy["passed"] is True  # 语义错误不挡结构 pass（结构=收尾正常+schema 干净）
    assert healthy["assertions"] == [] and healthy["judge"] is None
    assert healthy["stats"]["semantic_errors"] == 1

    assert score_episode_structural(record("done", [ok_inv, schema_bad]))["passed"] is False
    assert score_episode_structural(record("blocked", [ok_inv]))["passed"] is False
    assert score_episode_structural(record("infra_error", []))["passed"] is False
