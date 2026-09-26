"""``djdoctor shell`` — Django shell after verifying the project loads."""

from __future__ import annotations

import typer

from django_doctor.bridge.manage import run_interactive
from django_doctor.commands._common import fail_with_probe_error, get_state, handle_errors


def register(app: typer.Typer) -> None:
    @app.command("shell", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
    @handle_errors
    def shell(ctx: typer.Context) -> None:
        """Open `manage.py shell` (extra arguments are passed through, e.g. -i ipython)."""
        state = get_state(ctx)
        info = state.runner().run("info")
        if not info.ok:
            fail_with_probe_error(state, info, headline="The project cannot be loaded, so the shell would fail.")
        result = run_interactive(state.project(), ["shell", *ctx.args])
        raise typer.Exit(result.returncode if result.returncode >= 0 else 1)
