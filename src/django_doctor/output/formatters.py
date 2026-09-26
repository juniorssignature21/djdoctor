"""Human-readable rendering of diagnoses, migration plans and doctor reports."""

from __future__ import annotations

import json
from typing import Any

from rich.markup import escape

from django_doctor.diagnostics.models import DoctorReport, Status
from django_doctor.explain.models import Confidence, Diagnosis
from django_doctor.migration.planner import MigrationPlan
from django_doctor.migration.safety import OperationAssessment
from django_doctor.output.console import Console
from django_doctor.risk import Risk


def print_json(console: Console, data: Any) -> None:
    console.raw(json.dumps(data, indent=2, default=str))


def risk_markup(risk: Risk) -> str:
    return f"[risk.{risk.value}]{risk.label}[/risk.{risk.value}]"


# -------------------------------------------------------------- diagnosis
def render_diagnosis(console: Console, d: Diagnosis, *, show_raw: bool = False) -> None:
    c = console
    c.print("")
    c.print(f"[err]{escape(d.title)}[/err]" if d.risk is not Risk.NONE or d.category.value != "unknown" else f"[heading]{escape(d.title)}[/heading]")
    c.print(f"[muted]{escape(d.error_type)}[/muted]" if d.error_type else "")
    c.print("")
    _section(c, "What happened")
    for line in d.what_happened.splitlines():
        c.print(f"  {escape(line)}" if line.strip() else "")
    if d.request_path:
        c.print(f"  [muted]Request: {escape(d.request_path)}[/muted]")
    if d.location:
        c.print("")
        c.print("  [muted]Where (your code):[/muted]")
        for line in d.location.splitlines():
            c.print(f"    {escape(line.strip())}")
    if d.why:
        c.print("")
        _section(c, "Why it happened")
        c.print(f"  {escape(d.why)}")
    if d.evidence:
        c.print("")
        _section(c, "Evidence")
        for e in d.evidence:
            marker = "[ok]✓[/ok]" if e.verified else "[muted]•[/muted]"
            suffix = "" if e.verified else " [muted](from the error message)[/muted]"
            c.print(f"  {marker} {escape(e.text)}{suffix}")
    if d.causes:
        c.print("")
        for conf in (Confidence.DETECTED, Confidence.LIKELY, Confidence.POSSIBLE):
            items = [x for x in d.causes if x.confidence is conf]
            if not items:
                continue
            style = {"detected": "ok", "likely": "warn", "possible": "muted"}[conf.value]
            c.print(f"[{style}]{conf.label}:[/{style}]")
            for item in items:
                c.print(f"  {escape(item.text)}")
    if d.fixes:
        c.print("")
        _section(c, "Recommended fix")
        for i, fix in enumerate(d.fixes, 1):
            c.print(f"  {i}. {escape(fix)}")
    if d.commands:
        c.print("")
        _section(c, "Commands to try")
        for cmd in dict.fromkeys(d.commands):
            c.command(cmd)
    c.print("")
    note = f" — {escape(d.risk_note)}" if d.risk_note else ""
    c.print(f"[heading]Risk level:[/heading] {risk_markup(d.risk)}{note}")
    if d.generic:
        c.print("[muted]No specific rule matched this error; the advice above is general.[/muted]")
    if show_raw and d.error_message:
        c.print("")
        _section(c, "Original message (secrets redacted)")
        c.raw(d.error_message)


def _section(c: Console, title: str) -> None:
    c.print(f"[heading]{escape(title)}[/heading]")


# ------------------------------------------------------------------ plan
def render_operation(c: Console, op: OperationAssessment, indent: int = 4) -> None:
    pad = " " * indent
    risk = f"  [{'risk.' + op.risk.value}]({op.risk.label})[/{'risk.' + op.risk.value}]" if op.risk.rank >= 2 else ""
    c.print(f"{pad}{escape(op.symbol)} {escape(op.describe)}{risk}")
    if op.risk.rank >= 2 or c.verbose:
        for reason in op.reasons:
            c.print(f"{pad}    [muted]{escape(reason)}[/muted]")
    if op.data_at_risk:
        c.print(f"{pad}    [warn]Data at risk: {escape(op.data_at_risk)}[/warn]")


def render_plan(c: Console, plan: MigrationPlan, *, title: str = "Migration Plan") -> None:
    c.print(f"[heading]{escape(title)}[/heading]")
    c.print("")
    if plan.blocking_problems:
        for problem in plan.blocking_problems:
            c.error(problem)
        c.print("")
    if plan.db_missing_file:
        c.warning("The SQLite database file does not exist yet; every migration is pending.")
    elif plan.db_error:
        err = plan.db_error
        c.warning(f"Could not read applied migrations from the database ({err.get('type')}). "
                  "Pending migrations below are assumed.")

    if plan.changes:
        c.print("[heading]Model changes without a migration[/heading] [muted](makemigrations would create)[/muted]")
        by_app: dict[str, list] = {}
        for change in plan.changes:
            by_app.setdefault(change.app, []).append(change)
        for app, changes in by_app.items():
            c.print(f"  App: [bold]{escape(app)}[/bold]")
            for change in changes:
                c.print(f"    [muted]{escape(app)}/migrations/{escape(change.proposed_name)}.py[/muted]")
                for op in change.operations:
                    render_operation(c, op, indent=6)
        c.print("")

    forward = [m for m in plan.pending if not m.backwards]
    backward = [m for m in plan.pending if m.backwards]
    if forward:
        c.print(f"[heading]Migrations to apply[/heading] ({len(forward)})")
        compact = len(forward) > 8 and not c.verbose
        for m in forward:
            c.print(f"  {escape(m.label)}" + (" [muted](assumed unapplied)[/muted]" if m.assumed and not plan.db_missing_file else ""))
            if compact and m.risk.rank < 2:
                continue
            for op in m.operations:
                if op.risk.rank >= 1 or c.verbose or len(m.operations) <= 4:
                    render_operation(c, op, indent=4)
        c.print("")
    if backward:
        c.print(f"[heading]Migrations to UNAPPLY[/heading] ({len(backward)})")
        for m in backward:
            c.print(f"  [warn]{escape(m.label)}[/warn]")
            for op in m.operations:
                render_operation(c, op, indent=4)
        c.print("")

    if plan.unmigrated_apps:
        c.print("[heading]Apps with models but no migrations[/heading]")
        for app in plan.unmigrated_apps:
            c.print(f"  {escape(app['label'])}: {escape(', '.join(app['models'][:8]))}")
        c.print("")
    if plan.unresolved_questions:
        c.print("[heading]Needs a decision[/heading]")
        for q in plan.unresolved_questions:
            what = {"not_null_addition": "new NOT NULL field without a default",
                    "not_null_alteration": "field becomes NOT NULL",
                    "auto_now_add_addition": "new auto_now_add field needs a value for existing rows",
                    "unique_callable_default": "unique field with a callable default"}.get(q["kind"], q["kind"])
            c.print(f"  [warn]⚠[/warn] {escape(q['model'])}.{escape(q['field'])}: {escape(what)}")
        c.print("")
    if plan.rename_candidates:
        c.print("[heading]Ambiguous changes[/heading]")
        for r in plan.rename_candidates:
            label = "Django asked about a rename" if r.source == "django" else "possible rename (not confirmed)"
            if r.kind == "field":
                c.print(f"  [warn]⚠[/warn] {escape(r.model)}.{escape(r.old)} → {escape(r.new)}  [muted]({label})[/muted]")
            else:
                c.print(f"  [warn]⚠[/warn] model {escape(r.old)} → {escape(r.new)}  [muted]({label})[/muted]")
        c.print("  [muted]Django Doctor does not assume these are the same data.[/muted]")
        c.print("")
    if plan.ghost_migrations:
        c.warning("Recorded as applied but missing on disk: " + ", ".join(plan.ghost_migrations[:10]))
    for item in plan.missing_tables:
        c.error(f"Table {item['table']} ({item['model']}) is missing although its migrations are applied.")
    for item in plan.missing_columns:
        c.error(f"Column {item['table']}.{item['column']} ({item['model']}.{item['field']}) is missing although its migrations are applied.")

    if plan.all_operations or plan.rename_candidates:
        reasons = sorted({r for o in plan.all_operations if o.risk is plan.risk for r in o.reasons})
        c.print(f"[heading]Risk:[/heading] {risk_markup(plan.risk)}" + (f" — {escape(reasons[0])}" if reasons else ""))
    if plan.strategies:
        c.print("")
        c.print("[heading]Recommended strategy[/heading]")
        for s in plan.strategies:
            c.print(f"  [bold]{escape(s.title)}[/bold]")
            n = 0
            for step in s.steps:
                if step.startswith("  "):
                    c.print(f"      {escape(step.strip())}")
                else:
                    n += 1
                    c.print(f"    {n}. {escape(step)}")
    if plan.is_up_to_date and not plan.unmigrated_apps:
        c.success("No model changes and no unapplied migrations. Everything is up to date.")


# ---------------------------------------------------------------- doctor
_STATUS_STYLE = {
    Status.OK: ("ok", "ok"), Status.WARNING: ("warn", "warn"), Status.ERROR: ("err", "err"),
    Status.SKIPPED: ("skip", "muted"), Status.INFO: ("info", "info"),
}


def render_report(c: Console, report: DoctorReport, *, explain: bool = True) -> None:
    for category, results in report.by_category():
        worst = _worst(results)
        sym_key, style = _STATUS_STYLE[worst]
        c.print(f"[{style}]{c.symbols[sym_key]}[/{style}] [heading]{escape(category)}[/heading]")
        for r in results:
            if r.status is Status.OK and not c.verbose:
                continue
            sym_key, style = _STATUS_STYLE[r.status]
            if r.status is Status.OK:
                c.print(f"    [muted]{c.symbols['ok']} {escape(r.title)}[/muted]")
            else:
                c.print(f"  [{style}]{c.symbols[sym_key]} {escape(r.title)}[/{style}]")
            for d in r.details[: (50 if c.verbose else 8)]:
                c.print(f"      [muted]{escape(d)}[/muted]")
            if r.hint:
                c.print(f"      → {escape(r.hint)}")
            for cmd in r.commands:
                c.print(f"      [cmd]{escape(cmd)}[/cmd]")
        # Explanations for errors
        if explain:
            for r in results:
                if r.diagnosis is not None and r.status is Status.ERROR:
                    render_diagnosis(c, r.diagnosis)
                    c.print("")
    c.print("")
    c.print("[heading]Summary:[/heading]")
    c.print(f"  {_plural(report.errors, 'error')}")
    c.print(f"  {_plural(report.warnings, 'warning')}")
    c.print(f"  {_plural(report.passed, 'check')} passed")
    skipped = report.count(Status.SKIPPED)
    if skipped:
        c.print(f"  {skipped} skipped")


def _worst(results) -> Status:
    order = [Status.ERROR, Status.WARNING, Status.SKIPPED, Status.INFO, Status.OK]
    for status in order:
        if any(r.status is status for r in results):
            return status if status not in (Status.INFO,) else Status.OK
    return Status.OK


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"
