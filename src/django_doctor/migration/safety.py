"""Risk classification of migration operations.

Input is the operation dictionary produced by the probe (which read it from
Django's real migration objects / autodetector). Output says *why* something
is risky, in plain language. HIGH risk == potentially destroys data, and is
never applied without explicit confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from django_doctor.risk import Risk

_DESTRUCTIVE_SQL = re.compile(r"\b(DROP\s+(TABLE|COLUMN|SCHEMA|DATABASE|INDEX)|DELETE\s+FROM|TRUNCATE|ALTER\s+TABLE\s+\S+\s+DROP)\b", re.I)

_HARMLESS = {
    "AlterModelOptions", "AlterModelManagers", "AlterModelTableComment", "AddIndexConcurrently",
    "AlterIndexTogether", "RenameIndex",
}


@dataclass
class OperationAssessment:
    app: str
    model: str | None
    op_type: str
    describe: str
    risk: Risk
    reasons: list[str] = field(default_factory=list)
    backwards: bool = False
    data_at_risk: str | None = None
    field_name: str | None = None

    @property
    def destructive(self) -> bool:
        return self.risk is Risk.HIGH

    @property
    def symbol(self) -> str:
        if self.backwards:
            return "↶"
        return {"CreateModel": "+", "AddField": "+", "AddIndex": "+", "AddConstraint": "+",
                "DeleteModel": "-", "RemoveField": "-", "RemoveIndex": "-", "RemoveConstraint": "-",
                "AlterField": "~", "RenameField": "~", "RenameModel": "~"}.get(self.op_type, "•")

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app, "model": self.model, "operation": self.op_type, "describe": self.describe,
            "risk": self.risk.value, "reasons": self.reasons, "backwards": self.backwards,
            "destructive": self.destructive, "data_at_risk": self.data_at_risk, "field": self.field_name,
        }


def _label(app: str, model: str | None) -> str:
    return f"{model}" if model else app


def assess_operation(op: dict[str, Any], app: str, *, backwards: bool = False,
                     row_counts: dict[str, Any] | None = None) -> OperationAssessment:
    kind = op.get("type", "?")
    model = op.get("model")
    describe = op.get("describe") or kind
    a = OperationAssessment(app=app, model=model, op_type=kind, describe=describe, risk=Risk.LOW,
                            backwards=backwards, field_name=op.get("field_name"))
    if backwards:
        _assess_backwards(op, a)
    else:
        _assess_forwards(op, a)
    _attach_row_counts(op, a, row_counts or {})
    return a


def _assess_forwards(op: dict[str, Any], a: OperationAssessment) -> None:
    kind = a.op_type
    model = a.model or "?"
    name = op.get("field_name")
    if kind == "CreateModel":
        a.risk = Risk.NONE
    elif kind == "DeleteModel":
        a.risk = Risk.HIGH
        a.reasons.append(f"Deleting model {model} drops its table and every row in it.")
    elif kind == "AddField":
        f = op.get("field") or {}
        if f.get("many_to_many"):
            a.risk = Risk.NONE
        elif (not f.get("null") and not f.get("has_default") and not f.get("has_db_default")
              and not (f.get("blank") and f.get("empty_strings_allowed"))):
            a.risk = Risk.MEDIUM
            a.reasons.append(f"{model}.{name} is NOT NULL without a default: existing rows need a value.")
        elif f.get("unique") and not f.get("primary_key"):
            a.risk = Risk.MEDIUM
            a.reasons.append(f"{model}.{name} is unique: the migration fails if existing rows receive duplicate values.")
        elif not op.get("preserve_default", True):
            a.risk = Risk.LOW
            a.reasons.append("Uses a one-off default for existing rows.")
        else:
            a.risk = Risk.NONE
    elif kind == "RemoveField":
        a.risk = Risk.HIGH
        a.reasons.append(f"Removing {model}.{name} permanently deletes the column and the data stored in it.")
    elif kind == "AlterField":
        _assess_alter(op.get("old_field") or {}, op.get("field") or {}, a, model, name)
    elif kind in ("RenameField", "RenameModel"):
        a.risk = Risk.LOW
        a.reasons.append("Data is preserved; update any raw SQL, templates or external code that uses the old name.")
    elif kind == "AlterModelTable":
        a.risk = Risk.MEDIUM
        a.reasons.append("Renames the database table; raw SQL or external systems using the old name will break.")
    elif kind in ("AddConstraint", "AlterUniqueTogether"):
        a.risk = Risk.MEDIUM
        a.reasons.append("Fails if existing rows already violate the new constraint.")
    elif kind in ("RemoveIndex", "RemoveConstraint", "AddIndex"):
        a.risk = Risk.LOW
        if kind == "AddIndex":
            a.reasons.append("Building an index can lock large tables for a while.")
    elif kind == "RunSQL":
        sql = op.get("sql") or ""
        if _DESTRUCTIVE_SQL.search(sql):
            a.risk = Risk.HIGH
            a.reasons.append("Custom SQL contains DROP/DELETE/TRUNCATE statements.")
        else:
            a.risk = Risk.MEDIUM
            a.reasons.append("Custom SQL: Django Doctor cannot verify what it does. Review it.")
    elif kind == "RunPython" and str(op.get("code", "")).endswith("RunPython.noop"):
        a.risk = Risk.NONE
    elif kind == "RunPython":
        a.risk = Risk.MEDIUM
        a.reasons.append(f"Custom Python code ({op.get('code', '?')}) may modify data. Review it.")
    elif kind == "SeparateDatabaseAndState":
        inner = [assess_operation(o, a.app) for o in op.get("database_operations", [])]
        a.risk = Risk.highest(i.risk for i in inner) if inner else Risk.NONE
        for i in inner:
            a.reasons.extend(i.reasons)
        if not inner:
            a.reasons.append("Changes Django's model state only; no database changes.")
    elif kind in _HARMLESS or kind.startswith("Alter") and kind.endswith(("Options", "Managers", "Comment")):
        a.risk = Risk.NONE
    elif kind == "AlterOrderWithRespectTo":
        a.risk = Risk.LOW
    else:
        a.risk = Risk.UNKNOWN
        a.reasons.append(f"Unrecognised operation type {a.op_type}; review it manually.")


def _assess_alter(old: dict[str, Any], new: dict[str, Any], a: OperationAssessment, model: str, name: str | None) -> None:
    a.risk = Risk.LOW
    if not old:
        a.reasons.append("Previous field definition unknown; review the change.")
        a.risk = Risk.MEDIUM
        return
    if old.get("class") != new.get("class"):
        a.risk = Risk.MEDIUM
        a.reasons.append(f"Changes {model}.{name} from {old.get('class')} to {new.get('class')}: "
                         "conversion may fail or lose precision for existing values.")
    old_len, new_len = old.get("max_length"), new.get("max_length")
    if isinstance(old_len, int) and isinstance(new_len, int) and new_len < old_len:
        a.risk = Risk.HIGH
        a.reasons.append(f"Shrinks max_length of {model}.{name} from {old_len} to {new_len}: longer values "
                         "may be truncated (MySQL) or make the migration fail (PostgreSQL).")
    if old.get("null") and not new.get("null"):
        a.risk = Risk.highest([a.risk, Risk.MEDIUM])
        a.reasons.append(f"Makes {model}.{name} NOT NULL: fails if existing rows contain NULL.")
    if not old.get("unique") and new.get("unique"):
        a.risk = Risk.highest([a.risk, Risk.MEDIUM])
        a.reasons.append(f"Makes {model}.{name} unique: fails if duplicates exist.")
    if old.get("related_model") != new.get("related_model") and (old.get("related_model") or new.get("related_model")):
        a.risk = Risk.highest([a.risk, Risk.MEDIUM])
        a.reasons.append("Changes the relation target; existing ids may not match the new table.")


def _assess_backwards(op: dict[str, Any], a: OperationAssessment) -> None:
    kind = a.op_type
    model = a.model or "?"
    name = op.get("field_name")
    a.describe = f"Undo: {a.describe}"
    if kind == "CreateModel":
        a.risk = Risk.HIGH
        a.reasons.append(f"Unapplying drops the table of {model} and all its rows.")
    elif kind == "AddField":
        a.risk = Risk.HIGH
        a.reasons.append(f"Unapplying removes the column {model}.{name} and its data.")
    elif kind in ("RemoveField", "DeleteModel"):
        a.risk = Risk.LOW
        a.reasons.append("Re-creates the structure, but previously deleted data is NOT restored.")
    elif kind == "AlterField":
        _assess_alter(op.get("field") or {}, op.get("old_field") or {}, a, model, name)
    elif kind in ("RunPython", "RunSQL"):
        if not op.get("reversible", True):
            a.risk = Risk.HIGH
            a.reasons.append("This operation is irreversible: unapplying will fail (IrreversibleError).")
        else:
            a.risk = Risk.MEDIUM
            a.reasons.append("Runs custom reverse code; review it.")
    else:
        _assess_forwards(op, a)
        a.describe = f"Undo: {op.get('describe') or kind}"


def _attach_row_counts(op: dict[str, Any], a: OperationAssessment, counts: dict[str, Any]) -> None:
    if not a.destructive or not a.model:
        return
    key = f"{a.app}.{a.model.lower()}"
    field_key = f"{key}.{op.get('field_name')}"
    if field_key in counts and "non_null" in counts[field_key]:
        n = counts[field_key]["non_null"]
        a.data_at_risk = f"{n} row(s) currently have a value in this column"
    elif key in counts and "rows" in counts[key]:
        n = counts[key]["rows"]
        a.data_at_risk = f"the table currently contains {n} row(s)"
    else:
        return
    if n == 0:
        a.data_at_risk += " in this database (other environments such as production may differ)"


# -------------------------------------------------------------- ambiguity
@dataclass
class RenameCandidate:
    app: str
    model: str
    old: str
    new: str
    kind: str = "field"  # or "model"
    #: "django" when Django's autodetector itself considered it a rename;
    #: "heuristic" when Django Doctor paired a removal with an addition.
    source: str = "heuristic"

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def find_rename_candidates(changes: dict[str, list[dict[str, Any]]], questions: list[dict[str, Any]]) -> list[RenameCandidate]:
    """Pair removals with additions on the same model that might be renames."""
    out: list[RenameCandidate] = []
    asked = {(q.get("model", "").lower(), q.get("old"), q.get("new")) for q in questions if q.get("kind") == "rename_field"}
    for app, migrations in changes.items():
        removed: dict[str, list[dict[str, Any]]] = {}
        added: dict[str, list[dict[str, Any]]] = {}
        deleted_models, created_models = [], []
        for mig in migrations:
            for op in mig.get("operations", []):
                model = (op.get("model") or "").lower()
                if op["type"] == "RemoveField":
                    removed.setdefault(model, []).append(op)
                elif op["type"] == "AddField":
                    added.setdefault(model, []).append(op)
                elif op["type"] == "DeleteModel":
                    deleted_models.append(op)
                elif op["type"] == "CreateModel":
                    created_models.append(op)
        for model, rems in removed.items():
            for rem in rems:
                old_field = rem.get("old_field") or {}
                for add in added.get(model, []):
                    new_field = add.get("field") or {}
                    same_kind = old_field.get("class") == new_field.get("class") or not old_field
                    if not same_kind:
                        continue
                    source = "django" if (model, rem.get("field_name"), add.get("field_name")) in asked else "heuristic"
                    out.append(RenameCandidate(app, rem.get("model") or model, rem.get("field_name"), add.get("field_name"),
                                               "field", source))
        for d in deleted_models:
            for c in created_models:
                out.append(RenameCandidate(app, d.get("model") or "?", d.get("model") or "?", c.get("model") or "?", "model",
                                           "heuristic"))
    return out
