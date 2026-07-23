"""AC-06a：A 族 12 任务全部过 validate。"""

from pathlib import Path

from agent_assay.tasks.loader import load_task
from agent_assay.tasks.validate import validate_repo

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_a_family_passes_validate():
    report = validate_repo(REPO_ROOT)
    assert report.ok, [issue.model_dump() for issue in report.issues]
    assert report.tasks >= 12

    task_files = sorted((REPO_ROOT / "tasks" / "a").glob("*.yaml"))
    tasks = [load_task(p) for p in task_files]
    assert [t.id for t in tasks] == [f"A{i:02d}" for i in range(1, 13)]
    assert all(t.family == "a" for t in tasks)
    # R5：每条任务至少一条程序断言（validate 已强制，这里显式复核）
    for t in tasks:
        assert t.expected.final_state or t.expected.trajectory, t.id
