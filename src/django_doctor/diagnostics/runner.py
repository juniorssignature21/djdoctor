"""Run every doctor check and collect a :class:`DoctorReport`."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator

from django_doctor.bridge.runner import ProbeRunner
from django_doctor.diagnostics import (
    database,
    files,
    migrations,
    project_checks,
    settings,
    system,
    urls,
)
from django_doctor.diagnostics.context import DoctorContext
from django_doctor.diagnostics.models import CheckResult, DoctorReport, Status
from django_doctor.exceptions import DoctorError
from django_doctor.project import DjangoProject

log = logging.getLogger(__name__)

CheckFunc = Callable[[DoctorContext], Iterable[CheckResult]]

#: (category, check function). Order matters: earlier checks warm caches used later.
CHECKS: list[tuple[str, CheckFunc]] = [
    ("Project", project_checks.check_project),
    ("Settings", settings.check_settings),
    ("Dependencies", project_checks.check_dependencies),
    ("Environment", settings.check_environment),
    ("Security", settings.check_security),
    ("Database", database.check_database),
    ("Migrations", migrations.check_migrations),
    ("URLs", urls.check_urls),
    ("Templates", files.check_templates),
    ("Static files", files.check_static),
    ("System checks", system.check_system),
]


def iter_results(ctx: DoctorContext, skip: Iterable[str] = (), only: Iterable[str] = ()) -> Iterator[CheckResult]:
    skip = {s.lower() for s in skip}
    only = {o.lower() for o in only}
    for category, func in CHECKS:
        if only and category.lower() not in only:
            continue
        if category.lower() in skip:
            continue
        try:
            for result in func(ctx):
                if result.id.lower() in skip or result.id.split(".")[0].lower() in skip:
                    continue
                yield result
        except DoctorError as exc:
            yield CheckResult(f"{category.lower()}.failed", category, Status.ERROR, exc.message,
                              [exc.detail] if exc.detail else [], hint=exc.hint)
        except Exception as exc:  # never let one broken check hide the others
            log.debug("check %s crashed", category, exc_info=True)
            yield CheckResult(f"{category.lower()}.internal", category, Status.SKIPPED,
                              f"Django Doctor could not run this check ({type(exc).__name__}: {exc})")


def run_doctor(project: DjangoProject, runner: ProbeRunner | None = None, *, deploy: bool = False,
               skip: Iterable[str] = (), only: Iterable[str] = ()) -> DoctorReport:
    runner = runner or ProbeRunner(project)
    ctx = DoctorContext(project, runner, deploy=deploy)
    report = DoctorReport()
    skip = list(skip) + list(project.config.skip_checks)
    for result in iter_results(ctx, skip=skip, only=only):
        report.add(result)
    return report
