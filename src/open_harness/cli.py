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
