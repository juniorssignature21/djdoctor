"""Build a readable, risk-annotated migration plan from Django's real migration state.

All facts come from the probe, which uses Django's ``MigrationLoader``,
``MigrationExecutor.migration_plan`` and ``MigrationAutodetector`` — the same
machinery as ``makemigrations``/``migrate``. Nothing here guesses at the
schema; the only heuristic is :func:`find_rename_candidates`, which is always
labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django_doctor.migration.safety import (
    OperationAssessment,
    RenameCandidate,
    assess_operation,
    find_rename_candidates,
)
from django_doctor.risk import Risk


@dataclass
class PlannedMigration:
    app: str
    name: str
    operations: list[OperationAssessment]
    file: str | None = None
    backwards: bool = False
    #: True when the database could not be read and the migration is only
    #: *assumed* to be unapplied.
    assumed: bool = False

    @property
    def label(self) -> str:
        return f"{self.app}.{self.name}"

    @property
    def risk(self) -> Risk:
        return Risk.highest(op.risk for op in self.operations)

    def to_dict(self) -> dict[str, Any]:
        return {"app": self.app, "name": self.name, "file": self.file, "backwards": self.backwards,
                "assumed": self.assumed, "risk": self.risk.value,
                "operations": [o.to_dict() for o in self.operations]}


@dataclass
class ModelChange:
    """A migration that ``makemigrations`` would create."""

    app: str
    proposed_name: str
    operations: list[OperationAssessment]

    @property
    def risk(self) -> Risk:
        return Risk.highest(op.risk for op in self.operations)

    def to_dict(self) -> dict[str, Any]:
        return {"app": self.app, "proposed_name": self.proposed_name, "risk": self.risk.value,
                "operations": [o.to_dict() for o in self.operations]}


@dataclass
class Strategy:
    title: str
    steps: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "steps": self.steps}


@dataclass
class MigrationPlan:
    pending: list[PlannedMigration] = field(default_factory=list)
    changes: list[ModelChange] = field(default_factory=list)
    conflicts: dict[str, list[str]] = field(default_factory=dict)
    unmigrated_apps: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    rename_candidates: list[RenameCandidate] = field(default_factory=list)
    ghost_migrations: list[str] = field(default_factory=list)
    missing_tables: list[dict[str, Any]] = field(default_factory=list)
    missing_columns: list[dict[str, Any]] = field(default_factory=list)
    strategies: list[Strategy] = field(default_factory=list)
    applied_count: int = 0
    graph_error: dict[str, Any] | None = None
    autodetect_error: dict[str, Any] | None = None
    db_error: dict[str, Any] | None = None
    db_missing_file: bool = False
    inconsistent_history: dict[str, Any] | None = None

    # --------------------------------------------------------------- queries
    @property
    def all_operations(self) -> list[OperationAssessment]:
        ops = [o for m in self.pending for o in m.operations]
        ops += [o for c in self.changes for o in c.operations]
        return ops

    @property
    def destructive_operations(self) -> list[OperationAssessment]:
        return [o for o in self.all_operations if o.destructive]

    @property
    def risk(self) -> Risk:
        risks = [o.risk for o in self.all_operations]
        if self.rename_candidates:
            risks.append(Risk.HIGH)
        return Risk.highest(risks)

    @property
    def needs_makemigrations(self) -> bool:
        return bool(self.changes)

    @property
    def needs_migrate(self) -> bool:
        return bool(self.pending)

    @property
    def is_up_to_date(self) -> bool:
        return not (self.changes or self.pending or self.conflicts or self.blocking_problems)

    @property
    def unresolved_questions(self) -> list[dict[str, Any]]:
        """Questions ``makemigrations --noinput`` cannot answer (it would abort)."""
        return [q for q in self.questions if q.get("kind") in {
            "not_null_addition", "not_null_alteration", "auto_now_add_addition", "unique_callable_default"}]

    @property
    def blocking_problems(self) -> list[str]:
        problems = []
        if self.graph_error:
            problems.append(f"The migration graph could not be loaded: {self.graph_error.get('type')}: "
                            f"{self.graph_error.get('message', '').splitlines()[0] if self.graph_error.get('message') else ''}")
        if self.conflicts:
            for app, names in self.conflicts.items():
                problems.append(f"Conflicting migrations in '{app}': {', '.join(names)}")
        if self.inconsistent_history:
            problems.append(f"Inconsistent migration history: {self.inconsistent_history.get('message', '')}")
        if self.autodetect_error:
            problems.append(f"Model change detection failed: {self.autodetect_error.get('type')}: "
                            f"{self.autodetect_error.get('message', '')}")
        return problems

    def pending_forward(self) -> list[PlannedMigration]:
        return [m for m in self.pending if not m.backwards]

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk.value,
            "up_to_date": self.is_up_to_date,
            "pending": [m.to_dict() for m in self.pending],
            "changes": [c.to_dict() for c in self.changes],
            "conflicts": self.conflicts,
            "unmigrated_apps": self.unmigrated_apps,
            "questions": self.questions,
            "rename_candidates": [r.to_dict() for r in self.rename_candidates],
            "ghost_migrations": self.ghost_migrations,
            "schema": {"missing_tables": self.missing_tables, "missing_columns": self.missing_columns},
            "strategies": [s.to_dict() for s in self.strategies],
            "blocking_problems": self.blocking_problems,
            "applied_count": self.applied_count,
            "database_readable": self.db_error is None and not self.db_missing_file,
        }


def build_plan(data: dict[str, Any]) -> MigrationPlan:
    counts = data.get("row_counts") or {}
    plan = MigrationPlan(
        conflicts=data.get("conflicts") or {},
        unmigrated_apps=data.get("unmigrated_apps") or [],
        questions=data.get("questions") or [],
        ghost_migrations=data.get("ghost_migrations") or [],
        applied_count=data.get("applied_count") or 0,
        graph_error=data.get("graph_error"),
        autodetect_error=data.get("autodetect_error"),
        db_error=data.get("db_error"),
        db_missing_file=bool(data.get("db_missing_file")),
        inconsistent_history=data.get("inconsistent_history"),
    )
    schema = data.get("schema") or {}
    plan.missing_tables = [t for t in schema.get("missing_tables", []) if not t.get("app_has_pending")]
    plan.missing_columns = [c for c in schema.get("missing_columns", []) if not c.get("app_has_pending")]

    # A database that does not exist yet (or has never been migrated) holds no data.
    fresh_database = plan.db_missing_file or (plan.applied_count == 0 and not plan.db_error)
    fresh_models: set[tuple[str, str]] = set()

    def assess(op: dict[str, Any], app: str, backwards: bool = False) -> OperationAssessment:
        key = (app, (op.get("model") or "").lower())
        assessment = assess_operation(op, app, backwards=backwards, row_counts=counts)
        if not backwards:
            if op.get("type") == "CreateModel":
                fresh_models.add(key)
            elif fresh_database or key in fresh_models:
                _mark_no_existing_data(assessment, fresh_database)
        return assessment

    for entry in data.get("pending", []):
        backwards = bool(entry.get("backwards"))
        ops = [assess(op, entry["app"], backwards) for op in entry.get("operations", [])]
        plan.pending.append(PlannedMigration(entry["app"], entry["name"], ops, entry.get("file"),
                                             backwards, bool(entry.get("assumed"))))

    changes = data.get("changes") or {}
    for app, migrations in changes.items():
        for mig in migrations:
            ops = [assess(op, app) for op in mig.get("operations", [])]
            plan.changes.append(ModelChange(app, mig.get("name", "?"), ops))

    plan.rename_candidates = find_rename_candidates(changes, plan.questions)
    plan.strategies = recommend_strategies(plan)
    return plan


def _mark_no_existing_data(a: OperationAssessment, fresh_database: bool) -> None:
    """Operations on a table that does not exist yet cannot lose existing data."""
    if a.risk in (Risk.NONE, Risk.LOW) and a.op_type not in ("RunPython", "RunSQL"):
        return
    if a.op_type in ("RunPython", "RunSQL") and not fresh_database:
        return  # custom code may touch other, existing tables
    a.risk = Risk.NONE if a.op_type not in ("RunPython", "RunSQL") else Risk.LOW
    a.data_at_risk = None
    a.reasons = ["The table is created earlier in this plan, so it holds no existing data."
                 if not fresh_database else "The database is new, so there is no existing data."]


def recommend_strategies(plan: MigrationPlan) -> list[Strategy]:
    strategies: list[Strategy] = []
    renamed_pairs = set()
    for c in plan.rename_candidates:
        if c.kind == "field":
            renamed_pairs.add((c.app, c.model.lower(), c.old))
            renamed_pairs.add((c.app, c.model.lower(), c.new))
            strategies.append(Strategy(
                f"{c.model}.{c.old} removed and {c.model}.{c.new} added — possibly a rename",
                [
                    f"If this IS a rename (same data): use a RenameField migration so data is kept — "
                    f"run `makemigrations` interactively and answer 'y' to \"Was {c.model.lower()}.{c.old} renamed to "
                    f"{c.model.lower()}.{c.new}?\" (Django only asks when the definitions match), or write "
                    f"migrations.RenameField('{c.model.lower()}', '{c.old}', '{c.new}') by hand.",
                    "If it is NOT a rename, migrate safely in steps:",
                    f"  1. Add {c.new} (keep {c.old} for now)",
                    f"  2. Copy existing {c.old} values into {c.new} with a data migration (RunPython)",
                    "  3. Verify the data",
                    f"  4. Remove {c.old} in a later migration",
                ],
            ))
        else:
            strategies.append(Strategy(
                f"Model {c.old} deleted and {c.new} created — possibly a rename",
                [f"If it is a rename, use migrations.RenameModel('{c.old}', '{c.new}') to keep the table and its rows.",
                 f"Otherwise copy the data from {c.old} to {c.new} with a data migration before deleting {c.old}."],
            ))
    for op in plan.all_operations:
        if not op.destructive:
            continue
        if (op.app, (op.model or "").lower(), op.field_name) in renamed_pairs:
            continue
        if op.op_type == "RemoveField" and not op.backwards:
            strategies.append(Strategy(
                f"Removing {op.model}.{op.field_name}",
                ["Make sure no code, template or report still reads the field.",
                 "Back up the database (or export the column) before applying.",
                 "Prefer removing the field in a separate deploy, after the code stopped using it."],
            ))
        elif op.op_type == "DeleteModel" and not op.backwards:
            strategies.append(Strategy(
                f"Deleting model {op.model}",
                ["Export the table's data if it may be needed later.",
                 "Check for foreign keys from other apps and external systems.",
                 "Back up the database before applying."],
            ))
        elif op.backwards:
            strategies.append(Strategy(
                f"Unapplying {op.app} ({op.describe})",
                ["Unapplying migrations removes the tables/columns they created — with their data.",
                 "Back up the database first. Consider writing a new forward migration instead."],
            ))
        elif op.op_type == "AlterField":
            strategies.append(Strategy(
                f"Shrinking {op.model}.{op.field_name}",
                ["Check the longest existing value first (e.g. SELECT MAX(LENGTH(col)) ...).",
                 "Clean or truncate data deliberately with a data migration before shrinking."],
            ))
    for q in plan.unresolved_questions:
        if q["kind"] == "not_null_addition":
            strategies.append(Strategy(
                f"New NOT NULL field {q['model']}.{q['field']} without a default",
                [f"Option A: add default=... to {q['field']}.",
                 "Option B: add it with null=True, backfill values with a data migration, then set null=False.",
                 "Option C: run `makemigrations` interactively and provide a one-off default."],
            ))
        elif q["kind"] == "not_null_alteration":
            strategies.append(Strategy(
                f"{q['model']}.{q['field']} changes from nullable to NOT NULL",
                ["Fill existing NULL values with a data migration first, or provide a default.",
                 "Then change null=True to null=False."],
            ))
        else:
            strategies.append(Strategy(
                f"{q['model']}.{q['field']} needs a value for existing rows",
                ["Run `makemigrations` interactively and choose how existing rows get a value."],
            ))
    # De-duplicate by title while keeping order.
    seen, unique = set(), []
    for s in strategies:
        if s.title not in seen:
            seen.add(s.title)
            unique.append(s)
    return unique
