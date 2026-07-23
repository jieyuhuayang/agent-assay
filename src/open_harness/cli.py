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
    from .scoring.pipeline import score_episode, score_episode_structural
    from .tasks.loader import load_fixture, load_mandate, load_task

    root = root.resolve()
    if env not in ("mock", "testnet"):
        typer.echo(f"未知 env: {env}（支持 mock | testnet）", err=True)
        raise typer.Exit(2)
    testnet_client = None
    if env == "testnet":
        # 起跑前连通性预检：不可达就明确降级，绝不静默跳过（AC-11d / AC3.3）
        from .env.testnet import TestnetConfigError, TestnetExchangeEnv, TestnetUnavailableError

        try:
            probe = TestnetExchangeEnv()
            probe.ping()
        except (TestnetConfigError, TestnetUnavailableError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2)
        testnet_client = probe.client

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
        if env == "testnet":
            from .env.testnet import TestnetExchangeEnv

            exchange = TestnetExchangeEnv(client=testnet_client)
        else:
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
        if env == "testnet":
            # 结构评分（specs/11 D-k）：不跑任务断言，不进 leaderboard
            record.scoring = score_episode_structural(record)
        else:
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
    from .scoring.pipeline import score_episode, score_episode_structural
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
        if record.scoring is not None and record.scoring.get("mode") == "structural":
            # testnet 结构评分结果：重评保持结构口径（fixture 断言对它无意义）
            record.scoring = score_episode_structural(record)
            save_result(record, path)
            typer.echo(f"{record.task_id} passed={record.scoring['passed']} (structural)")
            continue
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


@app.command("serve-mcp")
def serve_mcp(
    env: str = typer.Option("mock", "--env", help="mock | testnet"),
    fixture: Path = typer.Option(
        Path("fixtures/std_account_1.yaml"), "--fixture", help="fixture 路径（相对 --root）"
    ),
    mandate: Path = typer.Option(
        Path("mandates/std_conservative.yaml"), "--mandate", help="mandate 路径（相对 --root）"
    ),
    root: Path = typer.Option(Path("."), "--root", help="仓库根目录"),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", help="request_confirmation 恒返回 approved（把玩演示用）"
    ),
) -> None:
    """把 registry 工具集挂成标准 MCP server（stdio；R7 反射，specs/10）。"""
    from .env.mock import MockExchangeEnv
    from .mcp_server import serve
    from .tasks.loader import load_fixture, load_mandate

    root = root.resolve()
    if env not in ("mock", "testnet"):
        typer.echo(f"未知 env: {env}（支持 mock | testnet）", err=True)
        raise typer.Exit(2)
    fixture_path = root / fixture
    mandate_path = root / mandate
    for path, kind in ((fixture_path, "fixture"), (mandate_path, "mandate")):
        if not path.is_file():
            typer.echo(f"{kind} 文件不存在: {path}", err=True)
            raise typer.Exit(2)

    if env == "testnet":
        from .env.testnet import TestnetConfigError, TestnetExchangeEnv, TestnetUnavailableError

        try:
            exchange = TestnetExchangeEnv()
            exchange.ping()
        except (TestnetConfigError, TestnetUnavailableError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2)
        typer.echo("testnet 模式：--fixture 不生效（账户状态来自 testnet 实盘假资金）", err=True)
    else:
        exchange = MockExchangeEnv(load_fixture(fixture_path))
    mandate_spec = load_mandate(mandate_path)
    # stdio 是协议通道：人类可读日志一律走 stderr（specs/10 §4）
    typer.echo(
        f"open-harness MCP server (stdio): env={env} fixture={fixture} mandate={mandate}",
        err=True,
    )
    serve(exchange, mandate_spec, auto_approve=auto_approve)
if __name__ == "__main__":
    app()
