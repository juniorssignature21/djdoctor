"""Database connectivity checks (read-only)."""

from __future__ import annotations

from collections.abc import Iterator

from django_doctor.branding import CLI_NAME
from django_doctor.diagnostics.context import DoctorContext
from django_doctor.diagnostics.models import CheckResult, Status
from django_doctor.explain.engine import explain_probe_error
from django_doctor.security import redact_text


def describe_database(conf: dict) -> str:
    engine = (conf.get("engine") or "?").rsplit(".", 1)[-1]
    if engine == "sqlite3":
        return f"sqlite3 ({conf.get('name')})"
    host = conf.get("host") or "localhost"
    port = f":{conf['port']}" if conf.get("port") else ""
    return f"{engine} {conf.get('name')}@{host}{port}"


def check_database(ctx: DoctorContext) -> Iterator[CheckResult]:
    if not ctx.settings_loaded:
        yield CheckResult("database.skipped", "Database", Status.SKIPPED, "Skipped: settings could not be loaded")
        return
    result = ctx.probe("database")
    if not result.ok:
        yield CheckResult("database.error", "Database", Status.ERROR, "Database connections could not be tested",
                          [redact_text(result.error_message)], diagnosis=ctx.explain(result))
        return
    confs = (ctx.info or {}).get("databases", {})
    for alias, entry in result.data.get("databases", {}).items():
        label = describe_database(confs.get(alias, {}))
        if entry.get("missing_file"):
            yield CheckResult(f"database.{alias}", "Database", Status.WARNING,
                              f"'{alias}': SQLite database file does not exist yet ({label})",
                              hint=f"`{CLI_NAME} migrate` will create it.", commands=[f"{CLI_NAME} migrate"])
        elif entry.get("ok"):
            yield CheckResult(f"database.{alias}", "Database", Status.OK, f"'{alias}' reachable: {label}")
        else:
            error = entry.get("error") or {}
            diagnosis = explain_probe_error(error, ctx.explain_context) if error else None
            yield CheckResult(f"database.{alias}", "Database", Status.ERROR, f"'{alias}' is not reachable: {label}",
                              [redact_text(f"{error.get('type')}: {error.get('message', '').strip().splitlines()[0] if error.get('message') else ''}")],
                              diagnosis=diagnosis)
