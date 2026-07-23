"""oh CLI 骨架。子命令随特性包落地：validate(FP02) run(FP06) score(FP08) report(FP12) serve-mcp(FP10)。"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="oh",
    help="Open Harness — fiduciary-execution benchmark for exchange agents",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Open Harness CLI."""
