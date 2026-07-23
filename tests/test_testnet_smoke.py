"""FP11 · AC-11e：8 条 `env: both` 抽样任务的 testnet 冒烟（结构断言，specs/11 §4）。

integration 标记：默认 deselect；需要 OH_TESTNET_API_KEY/SECRET 与网络。
缺 key → skip；网络不可达 → skip 并给出明确降级信息（AC3.3 允许的替代形态）。

结构口径：每条任务用内联结构化脚本跑完整 run_episode（真实 testnet 调用），
断言 schema_errors == 0 ∧ status == "done"；episode 里挂出的限价单由测试清理
（行情 50% 深度外的买单不会成交）。
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# specs/11 §4 定稿的抽样清单（与任务 YAML 的 env: both 同步，由本文件测试守护）
SAMPLED_TASKS = ["A01", "A02", "A03", "A06", "A11", "B01", "B03", "B07"]

pytestmark = pytest.mark.integration

_HAS_KEYS = bool(os.environ.get("OH_TESTNET_API_KEY")) and bool(
    os.environ.get("OH_TESTNET_API_SECRET")
)


def _report(text: str) -> dict:
    return {"tool": "report", "arguments": {"text": text, "status": "done"}}


def _build_scripts(env) -> dict[str, list[dict]]:
    """按实时行情组装结构化脚本（静态脚本无法引用动态价格）。"""
    ticker = env.get_ticker("BTCUSDT")
    deep_bid = (ticker.bid * Decimal("0.5")).quantize(Decimal("0.01"))
    return {
        # A01 限价买单基础：深度外限价买 0.001 BTC（挂而不成交）
        "A01": [
            {"tool": "get_ticker", "arguments": {"symbol": "BTCUSDT"}},
            {"tool": "place_order", "arguments": {
                "symbol": "BTCUSDT", "side": "buy", "type": "limit",
                "qty": "0.001", "price": str(deep_bid)}},
            {"tool": "get_open_orders", "arguments": {"symbol": "BTCUSDT"}},
            _report("已挂深度外限价买单（结构冒烟）"),
        ],
        # A02 市价卖出指定数量：极小额市价卖；余额不足时是语义错误，不破坏结构 pass
        "A02": [
            {"tool": "get_balances", "arguments": {}},
            {"tool": "place_order", "arguments": {
                "symbol": "BTCUSDT", "side": "sell", "type": "market", "qty": "0.0002"}},
            _report("市价卖出尝试完成（结构冒烟）"),
        ],
        # A03 按语义定位撤单：清点挂单（撤单 API 由测试清理路径覆盖）
        "A03": [
            {"tool": "get_open_orders", "arguments": {}},
            _report("挂单清点完成（结构冒烟）"),
        ],
        # A06 总成本约束：quote 预算市价买（binance quoteOrderQty 通道）
        "A06": [
            {"tool": "place_order", "arguments": {
                "symbol": "BNBUSDT", "side": "buy", "type": "market", "quote_qty": "10"}},
            _report("预算内市价买入尝试完成（结构冒烟）"),
        ],
        # A11 费率如实报告：交易规则查询
        "A11": [
            {"tool": "get_trading_rules", "arguments": {"symbol": "BTCUSDT"}},
            _report("费率查询完成（结构冒烟）"),
        ],
        "B01": [
            {"tool": "get_balances", "arguments": {}},
            _report("余额查询完成（结构冒烟）"),
        ],
        "B03": [
            {"tool": "get_open_orders", "arguments": {}},
            _report("挂单清点完成（结构冒烟）"),
        ],
        "B07": [
            {"tool": "get_balances", "arguments": {}},
            _report("如实报告持仓（结构冒烟）"),
        ],
    }


def test_sampled_tasks_structural():
    if not _HAS_KEYS:
        pytest.skip("缺 OH_TESTNET_API_KEY/OH_TESTNET_API_SECRET，跳过 testnet 冒烟")

    from agent_assay.agent.providers import ScriptedProvider
    from agent_assay.agent.runner import run_episode
    from agent_assay.env.testnet import TestnetExchangeEnv, TestnetUnavailableError
    from agent_assay.results import Fingerprint
    from agent_assay.tasks.loader import load_mandate, load_task

    env = TestnetExchangeEnv()
    try:
        env.ping()
    except TestnetUnavailableError as exc:
        pytest.skip(f"testnet 不可达，降级信息：{exc}")  # AC3.3 的明确降级形态

    scripts = _build_scripts(env)
    assert sorted(scripts) == sorted(SAMPLED_TASKS)

    created_orders: list[tuple[str, str]] = []
    try:
        for task_id in SAMPLED_TASKS:
            spec = load_task(REPO_ROOT / "tasks" / task_id[0].lower() / f"{task_id}.yaml")
            assert spec.env == "both", f"{task_id} 必须标 env: both（抽样清单与语料同步）"
            mandate = load_mandate(REPO_ROOT / spec.mandate)
            episode_env = TestnetExchangeEnv(client=env.client)
            record = run_episode(
                spec, episode_env, mandate, ScriptedProvider(scripts[task_id]),
                fingerprint=Fingerprint(
                    model="scripted", model_version="scripted-v0",
                    taskset_version="v0.1.0", git_commit="smoke",
                    timestamp="2026-07-24T00:00:00+00:00", temperature="0",
                ),
            )
            # 清理登记必须先于断言（M3 审查修复）：断言失败时已挂出的单
            # 也要进 created_orders，否则泄漏到共享 testnet 账户
            for inv in record.trajectory:
                if inv.get("tool") == "place_order" and inv.get("ok"):
                    receipt = inv.get("result") or {}
                    if receipt.get("status") in ("new", "partially_filled"):
                        created_orders.append((receipt["symbol"], receipt["order_id"]))
            schema_errors = [
                inv for inv in record.trajectory if inv.get("error_kind") == "schema_error"
            ]
            assert not schema_errors, f"{task_id} 出现 schema 错误: {schema_errors}"
            assert record.status == "done", f"{task_id} 未正常收尾: {record.status}"
    finally:
        for symbol, order_id in created_orders:  # 结构冒烟不留挂单（也顺带覆盖 cancel API）
            try:
                env.cancel_order(symbol, order_id)
            except Exception as exc:  # noqa: BLE001 —— 清理尽力而为，不掩盖主断言
                print(f"cleanup cancel {symbol}/{order_id} failed: {exc}")
