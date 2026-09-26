"""Rule registry and the context object handed to every rule.

A rule is a function ``(ctx: RuleContext) -> Diagnosis | None``. Rules are
tried in registration order (more specific first); the first diagnosis wins.

Guidelines for rule authors:

* Extract facts from the *structured* error (exception type, message groups,
  frames), not by grepping the whole output.
* Use ``ctx.project`` to verify hypotheses; mark verified facts with
  ``verified=True`` / ``Confidence.DETECTED``. Never state something as
  detected unless it was checked.
* Recommend deterministic commands. Never recommend destructive commands
  without saying they are destructive.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from django_doctor.exit_codes import ExitCode
from django_doctor.explain.context import ProjectContext
from django_doctor.explain.models import Category, Diagnosis
from django_doctor.explain.traceback import ExceptionInfo, ParsedError
from django_doctor.risk import Risk
from django_doctor.security import redact_text

RuleFunc = Callable[["RuleContext"], "Diagnosis | None"]
_RULES: list[tuple[str, RuleFunc]] = []


def rule(rule_id: str) -> Callable[[RuleFunc], RuleFunc]:
    def decorator(func: RuleFunc) -> RuleFunc:
        func.rule_id = rule_id  # type: ignore[attr-defined]
        _RULES.append((rule_id, func))
        return func

    return decorator


#: Evaluation order of rule modules (specific before general). Within a
#: module, rules run in definition order. The "generic" fallback always runs last.
MODULE_ORDER = ["migrations", "config", "database", "web", "models"]


def registered_rules() -> list[tuple[str, RuleFunc]]:
    def key(item: tuple[int, tuple[str, RuleFunc]]):
        index, (rule_id, func) = item
        module = func.__module__.rsplit(".", 1)[-1]
        position = MODULE_ORDER.index(module) if module in MODULE_ORDER else len(MODULE_ORDER)
        return (rule_id == "generic", position, index)

    return [entry for _, entry in sorted(enumerate(_RULES), key=key)]


@dataclass
class RuleContext:
    parsed: ParsedError
    project: ProjectContext | None = None

    @property
    def root(self) -> Path | None:
        return self.project.root if self.project else None

    @property
    def final(self) -> ExceptionInfo:
        return self.parsed.final

    def find(self, *names: str) -> ExceptionInfo | None:
        return self.parsed.find(*names)

    def search(self, pattern: str | re.Pattern[str], *names: str, flags: int = 0) -> tuple[ExceptionInfo, re.Match[str]] | None:
        """Search the message of exceptions (optionally filtered by type), newest first."""
        regex = re.compile(pattern, flags) if isinstance(pattern, str) else pattern
        for exc in reversed(self.parsed.exceptions):
            if names and not exc.is_a(*names):
                continue
            match = regex.search(exc.message)
            if match:
                return exc, match
        return None

    def new(
        self,
        rule_id: str,
        category: Category,
        what_happened: str,
        *,
        exc: ExceptionInfo | None = None,
        exit_code: ExitCode = ExitCode.GENERAL_ERROR,
        risk: Risk = Risk.LOW,
        why: str = "",
    ) -> Diagnosis:
        exc = exc or self.final
        frame = self.parsed.project_frame(self.root)
        location = None
        if frame is not None:
            location = frame.short(self.root)
            if frame.code:
                location += f"\n    {frame.code}"
        return Diagnosis(
            rule_id=rule_id,
            category=category,
            title=category.heading,
            what_happened=what_happened,
            why=why,
            error_type=exc.type,
            error_message=redact_text(exc.message.strip())[:2000],
            location=location,
            request_path=self.parsed.request_path,
            risk=risk,
            exit_code=exit_code,
        )


def close_matches(word: str, candidates, n: int = 3, cutoff: float = 0.6) -> list[str]:
    return difflib.get_close_matches(word, list(candidates), n=n, cutoff=cutoff)


def quote_list(items, limit: int = 5) -> str:
    items = list(items)
    shown = ", ".join(f"'{i}'" for i in items[:limit])
    return shown + (f" (+{len(items) - limit} more)" if len(items) > limit else "")
