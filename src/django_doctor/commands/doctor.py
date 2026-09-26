"""``djdoctor doctor`` — full diagnostic scan."""

from __future__ import annotations

import typer

from django_doctor.commands._common import get_state, handle_errors
from django_doctor.diagnostics.runner import run_doctor
from django_doctor.output.formatters import print_json, render_report


def register(app: typer.Typer) -> None:
    @app.command("doctor")
    @handle_errors
    def doctor(
        ctx: typer.Context,
        deploy: bool = typer.Option(False, "--deploy", help="Include Django's deployment (security) checks."),
        strict: bool = typer.Option(False, "--strict", help="Exit non-zero on warnings too."),
        skip: list[str] = typer.Option([], "--skip", help="Skip a category or check id (repeatable)."),
        only: list[str] = typer.Option([], "--only", help="Only run these categories (repeatable)."),
        json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
        no_explain: bool = typer.Option(False, "--no-explain", help="Do not print detailed explanations for errors."),
    ) -> None:
        """Scan the project for configuration, database, migration and code problems."""
        state = get_state(ctx)
        project = state.project()
        c = state.console
        if not json_output:
            c.title("")
            c.out(f"[muted]Inspecting {project.root} ...[/muted]")
            c.blank()
        report = run_doctor(project, state.runner(), deploy=deploy, skip=skip, only=only)
        if json_output:
            print_json(c, report.to_dict())
        else:
            render_report(c, report, explain=not no_explain)
        raise typer.Exit(int(report.exit_code(strict=strict)))
