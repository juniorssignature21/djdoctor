"""Deterministic explanation rules.

Import order == evaluation order: specific rules first, the generic fallback
last. To add a rule, write a function decorated with ``@rule("id")`` in one of
these modules (or a new one imported below).
"""

from django_doctor.explain.rules import (
    config as _config,  # noqa: F401  (2: env vars, settings, imports)
)
from django_doctor.explain.rules import (
    database,  # noqa: F401  (3: database)
    migrations,  # noqa: F401  (1: migration graph problems)
    models,  # noqa: F401  (5: ORM, checks, syntax, generic fallback)
    web,  # noqa: F401  (4: urls, templates, requests)
)
from django_doctor.explain.rules.base import RuleContext, registered_rules, rule

__all__ = ["RuleContext", "registered_rules", "rule"]
