"""``djdoctor info`` — summary of the detected project (secrets redacted)."""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.table import Table

from django_doctor.commands._common import fail_with_probe_error, get_state, handle_errors
from django_doctor.diagnostics.database import describe_database
from django_doctor.output.formatters import print_json
from django_doctor.security import MASK, redact_mapping


def register(app: typer.Typer) -> None:
    @app.command("info")
    @handle_errors
    def info(
        ctx: typer.Context,
        json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
    ) -> None:
        """Show what Django Doctor detected about the project. Secrets are never shown."""
        state = get_state(ctx)
        project = state.project()
        c = state.console
        result = state.runner().run("info")
        if not result.ok:
            fail_with_probe_error(state, result, headline="The project's settings could not be loaded.")
        data = redact_mapping(result.data)
        # The probe never returns the key itself; make that explicit.
        data["secret_key"] = {"set": data["secret_key"]["set"], "value": MASK if data["secret_key"]["set"] else None}
        if json_output:
            print_json(c, {"project": {"root": str(project.root), "manage_py": str(project.manage_py),
                                       "settings": project.settings_module, "python": project.python}, **data})
            return
        c.title("Project information")
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(style="bold")
        table.add_column()
        rows = [
            ("Project root", str(project.root)),
            ("manage.py", str(project.manage_py.relative_to(project.root))),
            ("Settings", f"{project.settings_module} (from {project.settings.origin})" if project.settings else "?"),
            ("Python", f"{data['python']['version']} ({data['python']['executable']})"),
            ("Django", data["django"]["version"]),
            ("DEBUG", str(data["debug"])),
            ("SECRET_KEY", MASK if data["secret_key"]["set"] else "(empty)"),
            ("ALLOWED_HOSTS", repr(data["allowed_hosts"])),
            ("ROOT_URLCONF", str(data.get("root_urlconf"))),
            ("AUTH_USER_MODEL", str(data.get("auth_user_model"))),
        ]
        for alias, db in data.get("databases", {}).items():
            pw = "password " + (MASK if db.get("has_password") else "(not set)")
            rows.append((f"Database '{alias}'", f"{describe_database(db)}, {pw}"))
        for k, v in rows:
            table.add_row(escape(k), escape(v))
        c.print(table)
        c.section("Installed apps")
        for a in data.get("installed_apps", []):
            local = a["path"].startswith(str(project.root))
            mig = "migrations" if a.get("has_migrations") else "no migrations"
            models = f"{len(a['models'])} models" if a["models"] else "no models"
            c.print(f"  {'[bold]' if local else '[muted]'}{escape(a['label'])}{'[/bold]' if local else '[/muted]'}"
                    f" [muted]({escape(a['name'])}; {models}; {mig})[/muted]")
