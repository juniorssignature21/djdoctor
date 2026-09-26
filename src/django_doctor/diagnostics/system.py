"""Django's own system check framework (``manage.py check``)."""

from __future__ import annotations

from collections.abc import Iterator

from django_doctor.diagnostics.check_kb import advice_for
from django_doctor.diagnostics.context import DoctorContext
from django_doctor.diagnostics.models import CheckResult, Status


def check_system(ctx: DoctorContext) -> Iterator[CheckResult]:
    if not ctx.settings_loaded:
        yield CheckResult("system.skipped", "System checks", Status.SKIPPED, "Skipped: settings could not be loaded")
        return
    result = ctx.probe("checks", {"deploy": ctx.deploy})
    if not result.ok:
        yield CheckResult("system.crash", "System checks", Status.ERROR, "Django's system checks crashed",
                          [f"{result.error_type}: {result.error_message.splitlines()[0] if result.error_message else ''}"],
                          diagnosis=ctx.explain(result))
        return
    messages = [m for m in result.data.get("messages", []) if not m.get("silenced")]
    errors = [m for m in messages if m["level"] >= 40]
    warnings = [m for m in messages if 30 <= m["level"] < 40]
    for m in errors + warnings:
        advice = advice_for(m.get("id"))
        details = [m["msg"]]
        if m.get("hint"):
            details.append(f"Django hint: {m['hint']}")
        if advice:
            details.append(advice.explanation)
        yield CheckResult(f"system.{m.get('id')}", "System checks", Status.ERROR if m in errors else Status.WARNING,
                          f"{m.get('id')}: {m.get('obj') or 'project'}", details, hint=advice.fix if advice else None)
    if not errors and not warnings:
        suffix = " (including deployment checks)" if ctx.deploy else ""
        yield CheckResult("system.ok", "System checks", Status.OK, f"System checks passed{suffix}")
