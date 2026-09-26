"""URL configuration checks."""

from __future__ import annotations

from collections.abc import Iterator

from django_doctor.branding import CLI_NAME
from django_doctor.diagnostics.context import DoctorContext
from django_doctor.diagnostics.models import CheckResult, Status
from django_doctor.explain.rules.base import close_matches


def check_urls(ctx: DoctorContext) -> Iterator[CheckResult]:
    if not ctx.settings_loaded:
        yield CheckResult("urls.skipped", "URLs", Status.SKIPPED, "Skipped: settings could not be loaded")
        return
    result = ctx.probe("urls")
    if not result.ok:
        yield CheckResult("urls.load", "URLs", Status.ERROR, "The URL configuration could not be loaded",
                          [f"{result.error_type}: {result.error_message.splitlines()[0] if result.error_message else ''}"],
                          diagnosis=ctx.explain(result))
        return
    patterns = result.data.get("patterns", [])
    names = {p["full_name"] for p in patterns if p.get("full_name")}
    yield CheckResult("urls.load", "URLs", Status.OK, f"URLconf loaded ({len(patterns)} patterns, {len(names)} named)")

    refs = ctx.references
    missing = [n for n in refs.unique("url_names") if n not in names]
    if missing:
        details = []
        for name in missing[:15]:
            where = ", ".join(refs.where("url_names", name, ctx.project.root))
            suggestion = close_matches(name, names, n=1)
            namespaced = [n for n in names if n.split(":")[-1] == name.split(":")[-1] and n != name]
            extra = f" — did you mean '{(namespaced or suggestion)[0]}'?" if (namespaced or suggestion) else ""
            details.append(f"'{name}' used at {where}{extra}")
        yield CheckResult("urls.unknown_names", "URLs", Status.ERROR,
                          f"{len(missing)} URL name(s) used in code/templates do not exist (NoReverseMatch at runtime)",
                          details, commands=[f"{CLI_NAME} urls"])
    elif refs.url_names:
        yield CheckResult("urls.names", "URLs", Status.OK, f"All {len(refs.unique('url_names'))} referenced URL names exist")
