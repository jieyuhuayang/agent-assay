"""oh CLI。子命令随特性包落地：validate(FP02) run(FP06) score(FP08) report(FP12) serve-mcp(FP10)。"""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(
    name="oh",
    help="Open Harness — fiduciary-execution benchmark for exchange agents",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Open Harness CLI."""


@app.command()
def validate(
    root: Path = typer.Option(
        Path("."), "--root", help="仓库根目录（含 tasks/ fixtures/ mandates/）"
    ),
) -> None:
    """任务/fixture/mandate 全量 lint：schema、引用完整性、R5/R6、R9 数据面。"""
    from .tasks.validate import validate_repo

    report = validate_repo(root)
    for issue in report.issues:
        typer.echo(f"[{issue.code}] {issue.file}: {issue.message}", err=True)
    summary = (
        f"tasks={report.tasks} fixtures={report.fixtures} mandates={report.mandates}"
    )
    if not report.ok:
        typer.echo(f"validate FAILED: {len(report.issues)} issue(s), {summary}", err=True)
        raise typer.Exit(1)
    typer.echo(f"validate OK ({summary})")


TASKSET_VERSION = "v0.1.0"


def _git_commit(root: Path) -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 —— 无 git 环境时指纹降级为 unknown，不阻塞
        return "unknown"


def _make_provider(model: str, root: Path, task_id: str):
    from .agent.providers import LiteLLMProvider, ScriptedProvider

    if model == "scripted":
        import yaml as _yaml

        script_path = root / "scripts" / f"{task_id}.yaml"
        if script_path.is_file():
            actions = _yaml.safe_load(script_path.read_text(encoding="utf-8")) or []
        else:
            actions = [
                {"tool": "report", "arguments": {"text": "scripted default: no script provided", "status": "blocked"}}
            ]
        return ScriptedProvider(actions)
    return LiteLLMProvider(model)


@app.command()
def run(
    model: str = typer.Option(..., "--model", help="litellm 模型名，或 scripted"),
    family: str = typer.Option("a,b,c", "--family", help="逗号分隔任务族"),
    env: str = typer.Option("mock", "--env", help="mock | testnet"),
    task: str = typer.Option(None, "--task", help="只跑指定任务 ID"),
    out: Path = typer.Option(None, "--out", help="结果输出目录"),
    root: Path = typer.Option(Path("."), "--root", help="仓库根目录"),
    judge_model: str = typer.Option(
        None, "--judge-model", help="judge 模型（litellm 名）；缺省不跑 judge（Q5）"
    ),
) -> None:
    """跑任务集并内联评分（AC-08g），逐任务落盘结果 JSON（含指纹，R11）。"""
    from datetime import datetime, timezone

    from .agent.runner import run_episode
    from .env.mock import MockExchangeEnv
    from .results import Fingerprint, save_result
    from .scoring.model import ScoringContext
    from .scoring.pipeline import score_episode
    from .tasks.loader import load_fixture, load_mandate, load_task

    root = root.resolve()
    if env != "mock":
        # testnet 集成在 FP11 落地；明确报错，不静默降级（KICKOFF 第 11 节）
        typer.echo(f"env={env} 尚未支持（testnet 随 FP11 落地）；请使用 --env mock", err=True)
        raise typer.Exit(2)

    families = {f.strip() for f in family.split(",") if f.strip()}
    selected = []
    for path in sorted((root / "tasks").rglob("*.yaml")):
        spec = load_task(path)
        if spec.family not in families:
            continue
        if spec.env not in (env, "both"):
            continue
        if task is not None and spec.id != task:
            continue
        selected.append(spec)
    if not selected:
        typer.echo("没有匹配的任务", err=True)
        raise typer.Exit(1)

    started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out if out is not None else root / "results" / f"{model.replace('/', '_')}-{started}"
    out_dir.mkdir(parents=True, exist_ok=True)
    git_commit = _git_commit(root)

    for spec in selected:
        provider = _make_provider(model, root, spec.id)
        fixture = load_fixture(root / spec.fixture)
        exchange = MockExchangeEnv(fixture)
        mandate = load_mandate(root / spec.mandate)
        record = run_episode(
            spec,
            exchange,
            mandate,
            provider,
            fingerprint=Fingerprint(
                model=provider.model_name,
                model_version=provider.model_version,
                taskset_version=TASKSET_VERSION,
                git_commit=git_commit,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                temperature="0",
            ),
        )
        # litellm 首个响应后才有精确版本号，回填指纹
        record.fingerprint.model_version = provider.model_version
        # 内联评分（AC-08g）：断言 + 统计恒确定；judge 仅在显式给出模型时运行（Q5）
        ctx = ScoringContext(mandate=mandate, rules=fixture.rules)
        record.scoring = score_episode(spec, record, ctx, judge_model=judge_model)
        save_result(record, out_dir / f"{spec.id}.json")
        typer.echo(
            f"{spec.id} status={record.status} passed={record.scoring['passed']} "
            f"steps={len(record.trajectory)}"
        )

    meta = {
        "model": model,
        "env": env,
        "families": sorted(families),
        "tasks": [s.id for s in selected],
        "taskset_version": TASKSET_VERSION,
        "git_commit": git_commit,
        "started": started,
    }
    import json as _json

    (out_dir / "meta.json").write_text(
        _json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    typer.echo(f"results -> {out_dir}")


@app.command()
def score(
    run_dir: Path = typer.Argument(..., help="oh run 产出的结果目录"),
    judge_model: str = typer.Option(
        None, "--judge-model", help="judge 模型（litellm 名），可替换重评；缺省不跑 judge"
    ),
    root: Path = typer.Option(Path("."), "--root", help="仓库根目录（含 tasks/）"),
) -> None:
    """对既有 run 目录离线（重）评分：断言 + 统计幂等重算，judge 可换模型（AC-08d）。"""
    import json as _json

    from .results import ResultRecord, save_result
    from .scoring.model import ScoringContext
    from .scoring.pipeline import score_episode
    from .tasks.loader import load_fixture, load_mandate, load_task

    root = root.resolve()
    files = sorted(p for p in run_dir.glob("*.json") if p.name != "meta.json")
    if not files:
        typer.echo(f"{run_dir} 下没有结果文件", err=True)
        raise typer.Exit(1)

    for path in files:
        record = ResultRecord.model_validate(
            _json.loads(path.read_text(encoding="utf-8"))
        )
        task_path = root / "tasks" / record.task_id[0].lower() / f"{record.task_id}.yaml"
        spec = load_task(task_path)
        ctx = ScoringContext(
            mandate=load_mandate(root / spec.mandate),
            rules=load_fixture(root / spec.fixture).rules,
        )
        record.scoring = score_episode(spec, record, ctx, judge_model=judge_model)
        save_result(record, path)  # 唯一落盘路径（R2 脱敏）
        judge_note = ""
        if record.scoring["judge"] is not None:
            judge_note = f" judge={record.scoring['judge']['quality']}"
        elif record.scoring["judge_error"]:
            judge_note = " judge=error"
        typer.echo(f"{record.task_id} passed={record.scoring['passed']}{judge_note}")
