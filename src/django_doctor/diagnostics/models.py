"""Result types for ``djdoctor doctor``."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from django_doctor.exit_codes import ExitCode


class Status(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"
    INFO = "info"


# Display order of categories in the report.
CATEGORIES = [
    "Project", "Dependencies", "Settings", "Environment", "Security", "Database",
    "Migrations", "URLs", "Templates", "Static files", "System checks",
]

#: Exit code used when a category has errors (highest priority first).
CATEGORY_EXIT = {
    "Project": ExitCode.PROJECT_NOT_FOUND,
    "Database": ExitCode.DATABASE_ERROR,
    "Migrations": ExitCode.MIGRATION_PROBLEM,
}


@dataclass
class CheckResult:
    id: str
    category: str
    status: Status
    title: str
    details: list[str] = field(default_factory=list)
    hint: str | None = None
    commands: list[str] = field(default_factory=list)
    #: A structured explanation (Diagnosis) when an error was explained.
    diagnosis: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "category": self.category, "status": self.status.value, "title": self.title,
            "details": self.details, "hint": self.hint, "commands": self.commands,
            "diagnosis": self.diagnosis.to_dict() if self.diagnosis is not None else None,
        }


@dataclass
class DoctorReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def by_category(self) -> list[tuple[str, list[CheckResult]]]:
        groups: dict[str, list[CheckResult]] = {}
        for r in self.results:
            groups.setdefault(r.category, []).append(r)
        ordered = [(c, groups.pop(c)) for c in CATEGORIES if c in groups]
        ordered += list(groups.items())
        return ordered

    def count(self, status: Status) -> int:
        return sum(1 for r in self.results if r.status is status)

    @property
    def errors(self) -> int:
        return self.count(Status.ERROR)

    @property
    def warnings(self) -> int:
        return self.count(Status.WARNING)

    @property
    def passed(self) -> int:
        return self.count(Status.OK)

    def exit_code(self, strict: bool = False) -> ExitCode:
        errored = [r for r in self.results if r.status is Status.ERROR]
        if not errored:
            if strict and self.warnings:
                return ExitCode.GENERAL_ERROR
            return ExitCode.SUCCESS
        categories = {r.category for r in errored}
        for category, code in CATEGORY_EXIT.items():
            if category in categories:
                return code
        return ExitCode.CONFIGURATION_ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {"errors": self.errors, "warnings": self.warnings, "passed": self.passed,
                        "skipped": self.count(Status.SKIPPED)},
            "results": [r.to_dict() for r in self.results],
        }
