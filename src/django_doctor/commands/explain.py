"""``djdoctor explain`` — explain a Django error from a file, a pipe, or the last captured error."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from django_doctor.ai.base import AIUnavailable
from django_doctor.ai.registry import explain_with_ai
from django_doctor.branding import CLI_NAME
from django_doctor.bridge.manage import TracebackRecorder
from django_doctor.commands._common import get_state, handle_errors
from django_doctor.exceptions import DoctorError
from django_doctor.exit_codes import ExitCode
from django_doctor.explain.context import ProjectContext
from django_doctor.explain.engine import explain_text
from django_doctor.explain.models import Diagnosis
from django_doctor.output.formatters import print_json, render_diagnosis
from django_doctor.state import AppState


def _project_context(state: AppState, inspect: bool) -> ProjectContext | None:
    if not inspect:
        return None
    try:
        project = state.project()
    except DoctorError:
        state.console.debug("No Django project detected; explaining from the error text only.")
        return None
    return ProjectContext(project, state.runner())


def _emit(state: AppState, diagnosis: Diagnosis, *, json_output: bool, raw: bool, ai: bool, ai_provider: str | None) -> None:
    c = state.console
    if json_output:
        print_json(c, diagnosis.to_dict())
        return
    render_diagnosis(c, diagnosis, show_raw=raw)
    if ai:
        config = None
        try:
            config = state.project().config.ai
        except DoctorError:
            pass
        c.print("")
        try:
            text, provider = explain_with_ai(diagnosis, provider=ai_provider, config=config)
        except AIUnavailable as exc:
            c.warning(f"AI explanation unavailable: {exc}")
            return
        c.print(f"[heading]AI explanation[/heading] [muted](via {provider}; unverified — commands are NOT executed)[/muted]")
        c.raw(text)


def register(app: typer.Typer) -> None:
    @app.command("explain")
    @handle_errors
    def explain(
        ctx: typer.Context,
        source: str = typer.Argument(None, help="Log file with the error ('-' for stdin). Default: the last captured error."),
        text: str = typer.Option(None, "--text", help="Explain this error text directly."),
        no_inspect: bool = typer.Option(False, "--no-inspect", help="Don't run project code to verify hypotheses."),
        no_echo: bool = typer.Option(False, "--no-echo", help="When reading a pipe, don't echo the input."),
        json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
        raw: bool = typer.Option(False, "--raw", help="Also print the original (redacted) error message."),
        ai: bool = typer.Option(False, "--ai", help="Add an optional AI explanation (sends the redacted report, never source code)."),
        ai_provider: str = typer.Option(None, "--ai-provider", help="AI provider name (overrides configuration)."),
    ) -> None:
        """Explain the latest Django error: what happened, why, evidence and what to do."""
        state = get_state(ctx)
        c = state.console
        pctx = _project_context(state, not no_inspect)
        emit_kwargs = dict(json_output=json_output, raw=raw, ai=ai, ai_provider=ai_provider)

        if text is None and source in (None, "-") and not _stdin_is_tty():
            if _explain_stream(state, pctx, echo=not no_echo and not json_output, **emit_kwargs):
                return
            if source == "-":
                c.warning("No input received on stdin.")
                raise typer.Exit(int(ExitCode.GENERAL_ERROR))

        if text is not None:
            content, origin = text, "--text"
        elif source not in (None, "-"):
            path = Path(source)
            if not path.is_file():
                raise DoctorError(f"File not found: {source}")
            content, origin = path.read_text(encoding="utf-8", errors="replace"), str(path)
        else:
            try:
                last = state.project().last_error_log
            except DoctorError:
                last = None
            if last is None or not last.is_file():
                raise DoctorError(
                    "No error to explain.",
                    detail="Django Doctor saves the last error seen by `djdoctor start`, `test`, `migrate` and "
                           "`django` commands. None has been captured in this project yet.",
                    hint=f"Pass a log file (`{CLI_NAME} explain error.log`), pipe output "
                         f"(`python manage.py runserver 2>&1 | {CLI_NAME} explain`) or use --text.",
                )
            content, origin = last.read_text(encoding="utf-8", errors="replace"), "last captured error"
        c.debug(f"Explaining error from {origin}")
        diagnosis = explain_text(content, pctx)
        if diagnosis is None:
            c.warning("No Python/Django error was found in the input.")
            raise typer.Exit(int(ExitCode.GENERAL_ERROR))
        _emit(state, diagnosis, **emit_kwargs)


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return True


def _explain_stream(state: AppState, pctx, *, echo: bool, **emit_kwargs) -> bool:
    """Read stdin line by line (e.g. from runserver), explaining each error as it completes.

    Returns False when stdin was empty (so the caller can fall back to the last saved error)."""
    c = state.console
    explained = []

    def on_error(tb: str) -> None:
        diagnosis = explain_text(tb, pctx)
        if diagnosis is not None:
            explained.append(diagnosis)
            if echo:
                c.print("")
                c.rule("Django Doctor")
            _emit(state, diagnosis, **emit_kwargs)
            if echo:
                c.rule()

    recorder = TracebackRecorder(on_error=on_error)
    buffer: list[str] = []
    try:
        for line in sys.stdin:
            if echo:
                sys.stdout.write(line)
                sys.stdout.flush()
            buffer.append(line)
            if len(buffer) > 5000:
                del buffer[:1000]
            recorder.feed(line)
    except KeyboardInterrupt:
        pass
    recorder.close()
    if explained:
        return True
    if not "".join(buffer).strip():
        return False
    diagnosis = explain_text("".join(buffer), pctx)
    if diagnosis is None:
        if not echo:
            c.warning("No Python/Django error was found in the input.")
        raise typer.Exit(int(ExitCode.GENERAL_ERROR))
    _emit(state, diagnosis, **emit_kwargs)
    return True
