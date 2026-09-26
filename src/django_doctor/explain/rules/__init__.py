"""Deterministic explanation rules.

Evaluation order is defined by ``base.MODULE_ORDER`` (not by import order).
To add a rule, write a function decorated with ``@rule("id")`` in one of these
modules (or a new module imported below and listed in MODULE_ORDER).
"""

from django_doctor.explain.rules import config, database, migrations, models, web  # noqa: F401
from django_doctor.explain.rules.base import RuleContext, registered_rules, rule

__all__ = ["RuleContext", "registered_rules", "rule"]
