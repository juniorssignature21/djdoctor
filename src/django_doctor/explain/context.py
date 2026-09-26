"""Lazy access to project facts used to *verify* hypotheses about an error.

Every accessor returns ``None`` when the information is unavailable (no
project, settings broken, database down...). Rules must treat ``None`` as
"unknown" and fall back to hedged language.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Any

from django_doctor.bridge.runner import ProbeRunner
from django_doctor.exceptions import DoctorError
from django_doctor.project import DjangoProject


class ProjectContext:
    def __init__(self, project: DjangoProject, runner: ProbeRunner | None = None):
        self.project = project
        self.runner = runner or ProbeRunner(project)

    @property
    def root(self) -> Path:
        return self.project.root

    def _probe(self, action: str, args: dict[str, Any] | None = None) -> dict[str, Any] | None:
        try:
            result = self.runner.run(action, args)
        except DoctorError:
            return None
        return result.data if result.ok else None

    @cached_property
    def info(self) -> dict[str, Any] | None:
        return self._probe("info")

    @cached_property
    def migrations(self) -> dict[str, Any] | None:
        return self._probe("migrations", {"schema": True})

    @cached_property
    def urls(self) -> dict[str, Any] | None:
        return self._probe("urls")

    def templates(self, names: list[str]) -> dict[str, Any] | None:
        return self._probe("templates", {"names": names})

    @cached_property
    def database(self) -> dict[str, Any] | None:
        return self._probe("database")

    def settings_files(self) -> list[Path]:
        return self.project.settings_files()

    # ------------------------------------------------------------- helpers
    def model_for_table(self, table: str) -> dict[str, Any] | None:
        data = self.migrations
        if not data:
            return None
        models = data.get("models", {})
        if table in models:
            return models[table]
        lowered = {k.lower(): v for k, v in models.items()}
        return lowered.get(table.lower())

    def url_names(self) -> list[str] | None:
        data = self.urls
        if data is None:
            return None
        return sorted({p["full_name"] for p in data.get("patterns", []) if p.get("full_name")})
