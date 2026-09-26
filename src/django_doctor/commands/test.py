"""``djdoctor test`` — run the test suite and explain environment failures."""

from __future__ import annotations

import typer

from django_doctor.branding import CLI_NAME
from django_doctor.bridge.manage import TracebackRecorder, run_streaming
from django_doctor.commands._common import (
    fail_with_probe_error,
    get_state,
    handle_errors,
    project_context,
    save_last_error,
)
from django_doctor.explain.engine import explain_text
from django_doctor.explain.models import Category
from django_doctor.output.formatters import render_diagnosis
from django_doctor.project import DjangoProject

#: Failures in these categories are about the environment, not a failing test.
_ENVIRONMENT_CATEGORIES = {Category.SETTINGS, Category.IMPORTS, Category.DATABASE, Category.MIGRATIONS,
                           Category.ENVIRONMENT, Category.SYNTAX}


def _uses_pytest(project: DjangoProject) -> bool:
    root = project.root
    for base in (root, root.parent):
        if (base / "pytest.ini").is_file():
            return True
        for name, marker in (("pyproject.toml", "[tool.pytest.ini_options]"), ("setup.cfg", "[tool:pytest]"),
                             ("tox.ini", "[pytest]")):
            f = base / name
            if f.is_file() and marker in f.read_text(encoding="utf-8", errors="replace"):
                return True
    return (root / "conftest.py").is_file()


def register(app: typer.Typer) -> None:
    @app.command("test", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
    @handle_errors
    def test(
        ctx: typer.Context,
        runner: str = typer.Option("auto", "--runner", help="auto, django or pytest."),
    ) -> None:
        """Run tests (manage.py test, or pytest when configured). Extra arguments are passed through."""
        state = get_state(ctx)
        c = state.console
        project = state.project()
        info = state.runner().run("info")
        if not info.ok:
            fail_with_probe_error(state, info, headline="The project cannot be loaded, so tests cannot run.")

        use_pytest = runner == "pytest" or (runner == "auto" and _uses_pytest(project))
        if use_pytest:
            pkgs = state.runner().run("packages", {"modules": ["pytest", "pytest_django"]}).data.get("modules", {})
            if not pkgs.get("pytest"):
                c.warning("pytest is configured but not installed; falling back to `manage.py test`.")
                use_pytest = False
        cmd = [project.python, "-m", "pytest", *ctx.args] if use_pytest else None
        c.info(f"Running {'pytest' if use_pytest else 'manage.py test'}")
        recorder = TracebackRecorder()
        result = run_streaming(project, ["test", *ctx.args], cmd=cmd, recorder=recorder, merge_stdout=True)
        if result.ok or result.interrupted:
            raise typer.Exit(result.returncode)
        text = result.captured_error or recorder.tail(60)
        save_last_error(state, text)
        diagnosis = explain_text(text, project_context(state)) if text else None
        c.print("")
        if diagnosis is not None and diagnosis.category in _ENVIRONMENT_CATEGORIES:
            c.error("The test run failed because of an environment/configuration problem:")
            render_diagnosis(c, diagnosis)
        else:
            c.print(f"[muted]Tests failed. For an explanation of the last error run `{CLI_NAME} explain`.[/muted]")
        raise typer.Exit(result.returncode if result.returncode > 0 else 1)
