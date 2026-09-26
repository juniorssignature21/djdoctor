"""Per-invocation state shared by all commands (console, options, project)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from django_doctor.bridge.runner import ProbeRunner
from django_doctor.output.console import Console
from django_doctor.project import DjangoProject, detect_project


@dataclass
class AppState:
    console: Console
    project_path: Path | None = None
    settings: str | None = None
    python: str | None = None
    debug: bool = False
    timeout: int = 120
    _project: DjangoProject | None = field(default=None, repr=False)
    _runner: ProbeRunner | None = field(default=None, repr=False)

    def project(self) -> DjangoProject:
        if self._project is None:
            self._project = detect_project(self.project_path, settings=self.settings, python=self.python)
            for note in self._project.notes:
                self.console.debug(note)
        return self._project

    def runner(self) -> ProbeRunner:
        if self._runner is None:
            self._runner = ProbeRunner(self.project(), timeout=self.timeout, debug=self.debug)
        return self._runner
