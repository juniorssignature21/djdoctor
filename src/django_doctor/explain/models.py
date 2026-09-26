"""Structured diagnosis produced by the explanation engine.

A :class:`Diagnosis` is the contract between the deterministic engine, the
renderers (terminal / JSON) and the optional AI layer. It separates what was
*verified* (``Evidence.verified``) from hypotheses (``Cause.confidence``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from django_doctor.exit_codes import ExitCode
from django_doctor.risk import Risk


class Confidence(str, Enum):
    #: Verified by inspecting the project / database.
    DETECTED = "detected"
    #: Strongly suggested by the error and typical for it.
    LIKELY = "likely"
    #: Plausible, not verified.
    POSSIBLE = "possible"

    @property
    def label(self) -> str:
        return {"detected": "Detected", "likely": "Likely cause", "possible": "Possible cause"}[self.value]


class Category(str, Enum):
    DATABASE = "database"
    MIGRATIONS = "migrations"
    URLS = "urls"
    TEMPLATES = "templates"
    SETTINGS = "settings"
    IMPORTS = "imports"
    ENVIRONMENT = "environment"
    MODELS = "models"
    VALIDATION = "validation"
    SECURITY = "security"
    SERVER = "server"
    SYNTAX = "syntax"
    UNKNOWN = "unknown"

    @property
    def heading(self) -> str:
        return {
            "database": "DATABASE ERROR",
            "migrations": "MIGRATION ERROR",
            "urls": "URL ERROR",
            "templates": "TEMPLATE ERROR",
            "settings": "CONFIGURATION ERROR",
            "imports": "IMPORT ERROR",
            "environment": "ENVIRONMENT ERROR",
            "models": "MODEL / QUERY ERROR",
            "validation": "VALIDATION ERROR",
            "security": "SECURITY / REQUEST ERROR",
            "server": "SERVER ERROR",
            "syntax": "SYNTAX ERROR",
            "unknown": "ERROR",
        }[self.value]


@dataclass
class Evidence:
    text: str
    #: True when Django Doctor verified this against the project/database;
    #: False when it is read from the error text only.
    verified: bool = False
    #: True when the text quotes project configuration (setting values, hosts,
    #: local paths). Shown locally, never sent to an AI provider.
    private: bool = False


@dataclass
class Cause:
    text: str
    confidence: Confidence = Confidence.POSSIBLE


@dataclass
class Diagnosis:
    rule_id: str
    category: Category
    title: str
    what_happened: str
    why: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    causes: list[Cause] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    risk: Risk = Risk.LOW
    risk_note: str = ""
    error_type: str = ""
    error_message: str = ""
    location: str | None = None
    request_path: str | None = None
    references: list[str] = field(default_factory=list)
    exit_code: ExitCode = ExitCode.GENERAL_ERROR
    #: True when no specific rule matched and only generic advice is given.
    generic: bool = False

    def add_evidence(self, text: str, verified: bool = False, *, private: bool = False) -> None:
        if not any(e.text == text for e in self.evidence):
            self.evidence.append(Evidence(text, verified, private))

    def add_cause(self, text: str, confidence: Confidence = Confidence.POSSIBLE) -> None:
        if not any(c.text == text for c in self.causes):
            self.causes.append(Cause(text, confidence))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        data["risk"] = self.risk.value
        data["exit_code"] = int(self.exit_code)
        data["causes"] = [{"text": c.text, "confidence": c.confidence.value} for c in self.causes]
        return data
