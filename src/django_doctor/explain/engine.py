"""Entry point of the explanation engine.

    text/traceback ──parse──▶ ParsedError ──rules (+ project facts)──▶ Diagnosis

The engine is fully deterministic. The optional AI layer (django_doctor.ai)
only ever receives the resulting :class:`Diagnosis`, never raw source code.
"""

from __future__ import annotations

import logging

from django_doctor.explain.context import ProjectContext
from django_doctor.explain.models import Diagnosis
from django_doctor.explain.rules import RuleContext, registered_rules
from django_doctor.explain.traceback import ParsedError, from_probe_error, parse_error

log = logging.getLogger(__name__)


def explain_parsed(parsed: ParsedError, project: ProjectContext | None = None) -> Diagnosis:
    ctx = RuleContext(parsed=parsed, project=project)
    for rule_id, func in registered_rules():
        try:
            diagnosis = func(ctx)
        except Exception:  # a buggy rule must never hide the user's error
            log.debug("rule %s crashed", rule_id, exc_info=True)
            continue
        if diagnosis is not None:
            return diagnosis
    raise AssertionError("the generic rule always matches")  # pragma: no cover


def explain_text(text: str, project: ProjectContext | None = None) -> Diagnosis | None:
    parsed = parse_error(text)
    if parsed is None:
        return None
    return explain_parsed(parsed, project)


def explain_probe_error(error: dict, project: ProjectContext | None = None) -> Diagnosis:
    return explain_parsed(from_probe_error(error), project)
