"""AC-01a：包结构与入口可用。"""

import typer


def test_package_importable_and_layout():
    import open_harness
    import open_harness.agent  # noqa: F401
    import open_harness.env  # noqa: F401
    import open_harness.env.fixture  # noqa: F401
    import open_harness.money  # noqa: F401
    import open_harness.net  # noqa: F401
    import open_harness.report  # noqa: F401
    import open_harness.results  # noqa: F401
    import open_harness.scoring  # noqa: F401
    import open_harness.secrets  # noqa: F401
    import open_harness.tasks.schema  # noqa: F401
    import open_harness.tools  # noqa: F401
    from open_harness.cli import app

    assert open_harness.__version__ == "0.1.0"
    assert isinstance(app, typer.Typer)
