"""``djdoctor check`` — Django system checks with explanations."""

from __future__ import annotations

import typer
from rich.markup import escape

from django_doctor.commands._common import fail_with_probe_error, get_state, handle_errors
from django_doctor.diagnostics.check_kb import advice_for
from django_doctor.exit_codes import ExitCode
from django_doctor.output.formatters import print_json


def register(app: typer.Typer) -> None:
    @app.command("check")
    @handle_errors
    def check(
        ctx: typer.Context,
        deploy: bool = typer.Option(False, "--deploy", help="Include deployment checks."),
        tag: list[str] = typer.Option([], "--tag", "-t", help="Only run checks with this tag (repeatable)."),
        database: list[str] = typer.Option([], "--database", help="Also run database checks for this alias."),
        fail_level: str = typer.Option("ERROR", "--fail-level", help="Level that causes a non-zero exit: ERROR or WARNING."),
        json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
    ) -> None:
        """Run Django's system check framework and explain the results."""
        state = get_state(ctx)
        c = state.console
        state.project()
        result = state.runner().run("checks", {"deploy": deploy, "tags": tag, "databases": database})
        if not result.ok:
            fail_with_probe_error(state, result, headline="Django could not be loaded, so checks could not run.")
        messages = [m for m in result.data.get("messages", []) if not m.get("silenced")]
        if json_output:
            print_json(c, messages)
        else:
            c.title("System checks")
            if not messages:
                c.success("System check identified no issues" + (" (including deployment checks)." if deploy else "."))
            for m in sorted(messages, key=lambda m: -m["level"]):
                style = "err" if m["level"] >= 40 else ("warn" if m["level"] >= 30 else "info")
                c.print(f"[{style}]{m['level_name']}[/{style}] [bold]{escape(m.get('id') or '')}[/bold] {escape(m.get('obj') or '')}")
                c.print(f"  {escape(m['msg'])}")
                if m.get("hint"):
                    c.print(f"  [muted]Django hint: {escape(m['hint'])}[/muted]")
                advice = advice_for(m.get("id"))
                if advice:
                    c.print(f"  Why: {escape(advice.explanation)}")
                    c.print(f"  Fix: {escape(advice.fix)}")
                c.print("")
        threshold = 30 if fail_level.upper().startswith("WARN") else 40
        if any(m["level"] >= threshold for m in messages):
            raise typer.Exit(int(ExitCode.CONFIGURATION_ERROR))
