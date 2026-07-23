"""AC-01a：包结构与入口可用。"""

import typer


def test_package_importable_and_layout():
    import agent_assay
    import agent_assay.agent  # noqa: F401
    import agent_assay.env  # noqa: F401
    import agent_assay.env.fixture  # noqa: F401
    import agent_assay.money  # noqa: F401
    import agent_assay.net  # noqa: F401
    import agent_assay.report  # noqa: F401
    import agent_assay.results  # noqa: F401
    import agent_assay.scoring  # noqa: F401
    import agent_assay.secrets  # noqa: F401
    import agent_assay.tasks.schema  # noqa: F401
    import agent_assay.tools  # noqa: F401
    from agent_assay.cli import app

    assert agent_assay.__version__ == "0.1.0"
    assert isinstance(app, typer.Typer)
