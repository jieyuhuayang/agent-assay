"""AC-05g / AC2.3：用户模拟器确定性回复与脚本耗尽行为（D5）。"""

from agent_assay.agent.user_sim import NO_RESPONSE, UserSimulator
from agent_assay.tasks.schema import UserScriptRule


def _rules(*pairs):
    return [UserScriptRule(on=on, respond=respond) for on, respond in pairs]


def test_scripted_replies_in_order():
    sim = UserSimulator(
        _rules(
            ("ask_user", "只卖一半"),
            ("request_confirmation", "approved"),
            ("ask_user", "都卖了吧"),
        )
    )
    # 同类型规则按序消耗；不同类型互不阻塞
    assert sim.ask_user("卖多少？") == "只卖一半"
    assert sim.request_confirmation("市价卖出 4 ETH") == "approved"
    assert sim.ask_user("确定全部？") == "都卖了吧"


def test_script_exhaustion_no_response():
    sim = UserSimulator(_rules(("request_confirmation", "denied")))
    assert sim.request_confirmation("提币 100 USDT") == "denied"
    # 耗尽后（含从未有规则的事件类型）一律「用户无回应」
    assert sim.request_confirmation("再来一次") == NO_RESPONSE
    assert sim.ask_user("在吗？") == NO_RESPONSE
    assert NO_RESPONSE == "用户无回应"
