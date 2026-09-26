"""``djdoctor urls`` — list URL patterns."""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.table import Table

from django_doctor.commands._common import fail_with_probe_error, get_state, handle_errors
from django_doctor.output.formatters import print_json


def register(app: typer.Typer) -> None:
    @app.command("urls")
    @handle_errors
    def urls(
        ctx: typer.Context,
        filter_: str = typer.Option(None, "--filter", "-f", help="Only show patterns whose route, name or view contains this text."),
        json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
    ) -> None:
        """List every URL pattern with its name and view."""
        state = get_state(ctx)
        c = state.console
        result = state.runner().run("urls")
        if not result.ok:
            fail_with_probe_error(state, result, headline="The URL configuration could not be loaded.")
        patterns = result.data.get("patterns", [])
        if filter_:
            needle = filter_.lower()
            patterns = [p for p in patterns if needle in " ".join(str(v) for v in p.values()).lower()]
        if json_output:
            print_json(c, patterns)
            return
        table = Table(show_lines=False, header_style="bold")
        table.add_column("Route")
        table.add_column("Name")
        table.add_column("View", overflow="fold")
        for p in patterns:
            table.add_row(escape("/" + p["pattern"]), escape(p.get("full_name") or "-"), escape(p["view"]))
        c.print(table)
        c.print(f"[muted]{len(patterns)} pattern(s)[/muted]")
