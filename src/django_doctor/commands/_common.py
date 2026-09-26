"""Helpers shared by command implementations."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

import typer

from django_doctor.bridge.runner import ProbeResult
from django_doctor.exceptions import DoctorError
from django_doctor.exit_codes import ExitCode
from django_doctor.explain.context import ProjectContext
from django_doctor.explain.engine import explain_probe_error, explain_text
from django_doctor.output.formatters import render_diagnosis
from django_doctor.state import AppState

F = TypeVar("F", bound=Callable[..., Any])


def get_state(ctx: typer.Context) -> AppState:
    state = ctx.find_root().obj
    assert isinstance(state, AppState), "CLI state not initialised"
    return state


def render_doctor_error(state: AppState, exc: DoctorError) -> None:
    c = state.console
    c.error(exc.message)
    if exc.detail:
        c.print("")
        c.raw(exc.detail)
    if exc.hint:
        c.print("")
        c.raw(exc.hint)


def handle_errors(func: F) -> F:
    """Render expected failures nicely; full traceback only with --debug."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = kwargs.get("ctx") or next((a for a in args if isinstance(a, typer.Context)), None)
        state = get_state(ctx) if ctx is not None else None
        try:
            return func(*args, **kwargs)
        except DoctorError as exc:
            if state is None or state.debug:
                raise
            render_doctor_error(state, exc)
            raise typer.Exit(int(exc.exit_code)) from None
        except KeyboardInterrupt:
            raise typer.Exit(int(ExitCode.INTERRUPTED)) from None

    return wrapper  # type: ignore[return-value]


def project_context(state: AppState) -> ProjectContext:
    return ProjectContext(state.project(), state.runner())


def fail_with_probe_error(state: AppState, result: ProbeResult, *, headline: str,
                          exit_code: ExitCode | None = None) -> None:
    """Explain a failed probe (settings/app loading error) and exit."""
    c = state.console
    c.error(headline)
    diagnosis = explain_probe_error(result.error or {}, project_context(state)) if result.error else None
    if diagnosis is not None:
        render_diagnosis(c, diagnosis)
        code = exit_code or diagnosis.exit_code
    else:
        code = exit_code or ExitCode.CONFIGURATION_ERROR
    if state.debug and result.error:
        c.stderr(result.error.get("traceback", ""), markup=False)
    elif result.error:
        c.print("")
        c.print("[muted]Run with --debug to see the full traceback.[/muted]")
    raise typer.Exit(int(code))


def explain_failure_text(state: AppState, text: str | None, *, inspect: bool = True) -> Any:
    """Explain captured command output; returns the Diagnosis (or None)."""
    if not text:
        return None
    ctx = None
    if inspect:
        try:
            ctx = project_context(state)
        except DoctorError:
            ctx = None
    diagnosis = explain_text(text, ctx)
    if diagnosis is not None:
        render_diagnosis(state.console, diagnosis)
    return diagnosis


def save_last_error(state: AppState, text: str | None) -> None:
    if not text:
        return
    try:
        project = state.project()
        project.ensure_state_dir()
        project.last_error_log.write_text(text, encoding="utf-8")
    except (OSError, DoctorError):
        pass
