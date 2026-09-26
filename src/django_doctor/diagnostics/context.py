"""Shared, lazily-computed facts for doctor checks."""

from __future__ import annotations

from functools import cached_property
from typing import Any

from django_doctor.bridge.runner import ProbeResult, ProbeRunner
from django_doctor.diagnostics.references import ProjectReferences, scan_references
from django_doctor.explain.context import ProjectContext
from django_doctor.explain.engine import explain_probe_error
from django_doctor.explain.models import Diagnosis
from django_doctor.project import DjangoProject


class DoctorContext:
    def __init__(self, project: DjangoProject, runner: ProbeRunner, *, deploy: bool = False):
        self.project = project
        self.runner = runner
        self.deploy = deploy
        self.explain_context = ProjectContext(project, runner)

    def probe(self, action: str, args: dict[str, Any] | None = None) -> ProbeResult:
        return self.runner.run(action, args)

    @cached_property
    def info_result(self) -> ProbeResult:
        return self.probe("info")

    @property
    def info(self) -> dict[str, Any] | None:
        return self.info_result.data if self.info_result.ok else None

    @property
    def settings_loaded(self) -> bool:
        return self.info_result.ok

    def explain(self, result: ProbeResult) -> Diagnosis | None:
        if result.ok or not result.error:
            return None
        return explain_probe_error(result.error, self.explain_context)

    @cached_property
    def references(self) -> ProjectReferences:
        exclude = set()
        info = self.info or {}
        static_root = (info.get("static") or {}).get("root")
        if static_root:
            from pathlib import Path

            exclude.add(Path(static_root))
        return scan_references(self.project.root, exclude=exclude)
