"""``djdoctor start`` — runserver with startup diagnostics and error capture."""

from __future__ import annotations

import sys

import typer
from rich.markup import escape

from django_doctor.branding import CLI_NAME
from django_doctor.bridge.manage import TracebackRecorder, run_streaming
from django_doctor.commands._common import (
    explain_failure_text,
    fail_with_probe_error,
    get_state,
    handle_errors,
    save_last_error,
)
from django_doctor.exit_codes import ExitCode
from django_doctor.explain.engine import explain_probe_error
from django_doctor.migration.planner import build_plan
from django_doctor.output.formatters import render_diagnosis


def _display_url(addrport: str | None) -> str:
    if not addrport:
        return "http://127.0.0.1:8000/"
    if addrport.isdigit():
        return f"http://127.0.0.1:{addrport}/"
    host, _, port = addrport.rpartition(":")
    return f"http://{host or '127.0.0.1'}:{port or '8000'}/"


def register(app: typer.Typer) -> None:
    @app.command("start", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
    @handle_errors
    def start(
        ctx: typer.Context,
        addrport: str = typer.Argument(None, help="Optional port or address:port (default 127.0.0.1:8000)."),
        no_preflight: bool = typer.Option(False, "--no-preflight", help="Skip startup diagnostics."),
        ignore_errors: bool = typer.Option(False, "--ignore-errors", help="Start even if diagnostics find errors."),
    ) -> None:
        """Run the development server after checking settings, database, checks and migrations.

        Extra arguments are passed to runserver (e.g. --noreload)."""
        state = get_state(ctx)
        c = state.console
        project = state.project()
        runner = state.runner()
        c.title("")
        failed = False
        if not no_preflight:
            c.success("Project detected")
            info = runner.run("info")
            if not info.ok:
                if ignore_errors:
                    c.error("Settings could not be loaded")
                    render_diagnosis(c, explain_probe_error(info.error or {}, None))
                    failed = True
                else:
                    fail_with_probe_error(state, info, headline="Settings could not be loaded")
            else:
                c.success("Settings loaded")
                db = runner.run("database").data.get("databases", {}).get("default", {})
                if db.get("missing_file"):
                    c.warning(f"SQLite database does not exist yet — run `{CLI_NAME} migrate` first")
                elif db.get("ok"):
                    c.success("Database reachable")
                else:
                    c.error("Database is not reachable")
                    if db.get("error"):
                        from django_doctor.commands._common import project_context

                        render_diagnosis(c, explain_probe_error(db["error"], project_context(state)))
                    failed = True
                checks = runner.run("checks", {})
                if checks.ok:
                    errors = [m for m in checks.data.get("messages", []) if m["level"] >= 40 and not m.get("silenced")]
                    if errors:
                        failed = True
                        c.error(f"System checks found {len(errors)} error(s) — see `{CLI_NAME} check`")
                        for m in errors[:5]:
                            c.detail(f"{m.get('id')}: {m.get('obj')}: {m['msg']}", indent=4)
                    else:
                        c.success("System checks passed")
                else:
                    failed = True
                    c.error("System checks crashed")
                    render_diagnosis(c, explain_probe_error(checks.error or {}, None))
                if db.get("ok"):
                    mig = runner.run("migrations", {"schema": False})
                    if mig.ok:
                        plan = build_plan(mig.data)
                        pending = plan.pending_forward()
                        if pending:
                            c.warning(f"{len(pending)} unapplied migration(s) — run `{CLI_NAME} migrate`")
                        if plan.changes:
                            c.warning(f"Model changes without migrations — run `{CLI_NAME} migrate`")
            if failed and not ignore_errors:
                c.print("")
                c.error("Not starting the server. Fix the problems above or pass --ignore-errors.")
                raise typer.Exit(int(ExitCode.CONFIGURATION_ERROR))

        c.print("")
        c.print("Starting development server...")
        c.print("")
        c.print(f"[bold]{escape(_display_url(addrport))}[/bold]")
        c.print("")

        def on_error(tb: str) -> None:
            save_last_error(state, tb)
            sys.stderr.write(f"\n[{CLI_NAME}] Error captured. Run `{CLI_NAME} explain` for an explanation.\n\n")
            sys.stderr.flush()

        recorder = TracebackRecorder(on_error=on_error)
        args = ["runserver", *([addrport] if addrport else []), *ctx.args]
        result = run_streaming(project, args, recorder=recorder)
        if result.interrupted or result.returncode in (0, 130, -2):
            return
        c.print("")
        c.error(f"The development server stopped (exit code {result.returncode}).")
        text = result.captured_error or recorder.tail(40)
        save_last_error(state, text)
        explain_failure_text(state, text)
        raise typer.Exit(int(ExitCode.GENERAL_ERROR))
