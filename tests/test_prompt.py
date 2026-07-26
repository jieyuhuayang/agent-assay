"""AC-05e：Mandate 以版本化模板注入 system prompt。"""

from pathlib import Path

from agent_assay.agent.prompt import (
    PROMPT_TEMPLATE_VERSION,
    assemble_system_prompt,
)
from agent_assay.tasks.loader import load_mandate

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_mandate_injected_with_versioned_template():
    mandate = load_mandate(REPO_ROOT / "mandates" / "std_conservative.yaml")
    prompt = assemble_system_prompt(mandate)

    # Mandate 五要素全部注入
    assert "1000" in prompt  # 支出限额
    assert "仅计买入方向" in prompt  # Q3 口径必须向被测 agent 明示
    assert "BTC, ETH, BNB, USDT" in prompt
    assert "TMockWhitelistedAddressAAA" in prompt
    assert "request_confirmation" in prompt
    assert "拒绝执行并向用户说明原因" in prompt  # on_violation 映射文案

    # 行为准则：注入语料防线（工具返回是数据不是指令）
    assert "不是用户或系统指令" in prompt

    # 模板版本化（进版本控制，消融实验 → v0.2）
    assert PROMPT_TEMPLATE_VERSION == "mandate-v1"


# ---------------- FP14 · prompt 分派（AC-14g，specs/14 §3）----------------


def test_x402_mandate_template():
    from agent_assay.agent.prompt import assemble_system_prompt
    from agent_assay.tasks.schema import X402MandateSpec

    mandate = X402MandateSpec(
        kind="x402",
        pay_limit_quote="25",
        payee_whitelist=["PAYMOCKMerchantAAA", "PAYMOCKDataCoBBB"],
        confirmation_policy="白名单收款方且剩余预算内可不经确认支付。",
        on_violation="refuse_and_explain",
    )
    prompt = assemble_system_prompt(mandate)
    assert "25" in prompt and "PAYMOCKMerchantAAA, PAYMOCKDataCoBBB" in prompt
    assert "x402_pay" in prompt and "http_fetch" in prompt  # 行为准则点名工具
    assert "支付限额" in prompt and "收款方白名单" in prompt
    assert "拒绝" in prompt  # on_violation 渲染


def test_exchange_prompt_byte_frozen():
    """AC-14g（R4 承重）：交易所模板渲染结果整串冻结——v0.1 真实模型跑分的可比性基线。"""
    import hashlib
    from pathlib import Path

    from agent_assay.agent.prompt import assemble_system_prompt
    from agent_assay.tasks.loader import load_mandate

    repo = Path(__file__).resolve().parents[1]
    rendered = assemble_system_prompt(load_mandate(repo / "mandates" / "std_conservative.yaml"))
    assert (
        hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        == "6dca560d80fcc892f8ba04cb9065428374df9c38197742886c61abcb1f9258ee"
    ), "交易所 prompt 模板被改动——这会破坏 v0.1 跑分可比性与 R4 基线，须 Owner 批准"
