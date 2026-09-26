"""Execute migrations through Django's own management commands.

Django Doctor never writes migration files or schema changes itself: it calls
``makemigrations`` / ``migrate`` in the project's interpreter, after the
safety analysis in :mod:`planner` has been shown and confirmed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django_doctor.bridge.manage import (
    CommandResult,
    MigrationSnapshot,
    run_captured,
    run_interactive,
    run_streaming,
)
from django_doctor.project import DjangoProject

_APPLYING = re.compile(r"^\s*(Applying|Unapplying) (?P<migration>[\w.]+)\.\.\.\s*(?P<status>OK|FAKED)?", re.M)


@dataclass
class CreateResult:
    result: CommandResult
    new_files: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result.ok


@dataclass
class ApplyResult:
    result: CommandResult
    applied: list[str] = field(default_factory=list)
    failed_on: str | None = None

    @property
    def ok(self) -> bool:
        return self.result.ok


def migration_directories(project: DjangoProject, info: dict[str, Any] | None, apps: list[str] | None = None) -> list[Path]:
    """Migration folders of apps that live inside the project."""
    dirs: list[Path] = []
    root = project.root.resolve()
    for app in (info or {}).get("installed_apps", []):
        if apps and app["label"] not in apps:
            continue
        path = Path(app["migrations_path"]) if app.get("migrations_path") else Path(app["path"]) / "migrations"
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        dirs.append(path)
    return dirs


def create_migrations(
    project: DjangoProject,
    info: dict[str, Any] | None,
    *,
    apps: list[str] | None = None,
    name: str | None = None,
    interactive: bool = False,
) -> CreateResult:
    dirs = migration_directories(project, info, apps)
    before = MigrationSnapshot.take(dirs)
    args = ["makemigrations", *(apps or [])]
    if name:
        args += ["--name", name]
    if interactive:
        result = run_interactive(project, args)
    else:
        result = run_captured(project, [*args, "--noinput"])
    after = MigrationSnapshot.take(dirs)
    return CreateResult(result, before.new_files(after))


def apply_migrations(
    project: DjangoProject,
    *,
    app_label: str | None = None,
    migration_name: str | None = None,
    database: str | None = None,
    stream: bool = True,
    out=None,
) -> ApplyResult:
    args = ["migrate"]
    if app_label:
        args.append(app_label)
        if migration_name:
            args.append(migration_name)
    if database:
        args += ["--database", database]
    args.append("--noinput")
    if stream:
        result = run_streaming(project, args, merge_stdout=True, out=out)
        output = result.stdout
    else:
        result = run_captured(project, args)
        output = result.stdout + result.stderr
    applied = [m.group("migration") for m in _APPLYING.finditer(output) if m.group("status")]
    failed_on = None
    if not result.ok:
        started = [m.group("migration") for m in _APPLYING.finditer(output)]
        if started and (not applied or started[-1] != applied[-1]):
            failed_on = started[-1]
    return ApplyResult(result, applied, failed_on)
