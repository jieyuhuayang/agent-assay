"""AC-09b/09c：C 族 14 条全过 validate、D-c 规范 tag 正确、R6 语料全量干净。"""

from pathlib import Path

from agent_assay.tasks.loader import load_task
from agent_assay.tasks.validate import scan_r6, validate_repo

REPO_ROOT = Path(__file__).resolve().parents[1]
C_IDS = [f"C{i:02d}" for i in range(1, 15)]

# specs/09 的规范位 tag 表（D-c 分母口径的唯一事实源之一，改动须同步 specs/09）
EXPECTED_TAGS = {
    "C01": {"irreversible", "overreach_bait"},
    "C02": {"must_clarify"},
    "C03": {"overreach_bait"},
    "C04": {"overreach_bait"},
    "C05": {"irreversible"},
    "C06": {"irreversible"},
    "C07": {"irreversible"},
    "C08": {"irreversible", "overreach_bait"},
    "C09": {"irreversible", "overreach_bait"},
    "C10": {"irreversible"},
    "C11": set(),
    "C12": {"irreversible", "overreach_bait"},
    "C13": {"legit_reverse", "irreversible"},
    "C14": {"legit_reverse", "irreversible"},
}
CANONICAL = {"irreversible", "overreach_bait", "must_clarify", "legit_reverse"}


def load_c_tasks():
    return {tid: load_task(REPO_ROOT / "tasks" / "c" / f"{tid}.yaml") for tid in C_IDS}


def test_c_family_validate_and_tags():
    report = validate_repo(REPO_ROOT)
    assert report.ok, [f"{i.code}: {i.file} {i.message}" for i in report.issues]

    tasks = load_c_tasks()
    assert set(tasks) == set(C_IDS)
    for tid, task in tasks.items():
        assert task.family == "c", tid
        assert set(task.tags) & CANONICAL == EXPECTED_TAGS[tid], f"{tid} 规范位 tag 不符"
        assert task.expected.judge_rubric, f"{tid} C 族必须配 judge_rubric"
        # R5 在 validate 兜底；此处确认每条至少一条程序断言
        assert task.expected.final_state or task.expected.trajectory, tid
    # 反向题必须成对存在（README 对照组设计的结构前提）
    assert EXPECTED_TAGS["C13"] >= {"legit_reverse"} and EXPECTED_TAGS["C14"] >= {"legit_reverse"}


def test_c_family_corpus_passes_r6_scan():
    """AC-09c：全量 C 族语料（题面 + user_script + judge_rubric）+ redteam fixture 过 R6。"""
    for tid, task in load_c_tasks().items():
        corpus = "\n".join(
            [task.instruction, task.expected.judge_rubric or ""]
            + [rule.respond for rule in task.user_script]
        )
        assert scan_r6(corpus) == [], f"{tid} 语料触发 R6 模式"
    fixture_text = (REPO_ROOT / "fixtures" / "redteam_1.yaml").read_text(encoding="utf-8")
    assert scan_r6(fixture_text) == []
