from django_doctor.migration.planner import build_plan
from django_doctor.migration.safety import assess_operation, find_rename_candidates
from django_doctor.risk import Risk


def op(kind, **kw):
    return {"type": kind, "describe": kind, **kw}


def test_forward_risk_levels():
    assert assess_operation(op("CreateModel", model="A"), "app").risk is Risk.NONE
    assert assess_operation(op("DeleteModel", model="A"), "app").risk is Risk.HIGH
    assert assess_operation(op("RemoveField", model="A", field_name="f"), "app").destructive
    assert assess_operation(op("RenameField", model="A"), "app").risk is Risk.LOW
    assert assess_operation(op("RunPython", code="x.y"), "app").risk is Risk.MEDIUM
    assert assess_operation(op("RunPython", code="django.db.migrations.operations.special.RunPython.noop"), "app").risk is Risk.NONE
    assert assess_operation(op("RunSQL", sql="DROP TABLE x"), "app").risk is Risk.HIGH
    assert assess_operation(op("RunSQL", sql="CREATE INDEX i ON t (c)"), "app").risk is Risk.MEDIUM
    assert assess_operation(op("SomethingNew"), "app").risk is Risk.UNKNOWN


def test_add_field_defaults():
    nullable = op("AddField", model="A", field_name="f", field={"null": True})
    assert assess_operation(nullable, "app").risk is Risk.NONE
    blank_char = op("AddField", model="A", field_name="f", field={"blank": True, "empty_strings_allowed": True})
    assert assess_operation(blank_char, "app").risk is Risk.NONE
    no_default = op("AddField", model="A", field_name="f", field={"null": False})
    assert assess_operation(no_default, "app").risk is Risk.MEDIUM


def test_alter_field_shrink_is_high():
    a = assess_operation(op("AlterField", model="A", field_name="f",
                            old_field={"class": "CharField", "max_length": 100},
                            field={"class": "CharField", "max_length": 10}), "app")
    assert a.risk is Risk.HIGH


def test_alter_field_null_to_not_null_is_medium():
    a = assess_operation(op("AlterField", model="A", field_name="f",
                            old_field={"class": "CharField", "null": True}, field={"class": "CharField", "null": False}), "app")
    assert a.risk is Risk.MEDIUM


def test_backwards_create_model_is_destructive():
    a = assess_operation(op("CreateModel", model="A"), "app", backwards=True)
    assert a.risk is Risk.HIGH and a.describe.startswith("Undo")
    irreversible = assess_operation(op("RunPython", reversible=False), "app", backwards=True)
    assert irreversible.risk is Risk.HIGH


def test_row_counts_are_attached():
    a = assess_operation(op("RemoveField", model="Student", field_name="phone"), "students",
                         row_counts={"students.student.phone": {"non_null": 42}})
    assert "42" in a.data_at_risk
    zero = assess_operation(op("RemoveField", model="Student", field_name="phone"), "students",
                            row_counts={"students.student.phone": {"non_null": 0}})
    assert "other environments" in zero.data_at_risk


def test_rename_candidates():
    changes = {"students": [{"operations": [
        op("RemoveField", model="Student", field_name="phone", old_field={"class": "CharField"}),
        op("AddField", model="Student", field_name="phone_number", field={"class": "CharField"}),
        op("AddField", model="Student", field_name="age", field={"class": "IntegerField"}),
    ]}]}
    candidates = find_rename_candidates(changes, [])
    assert [(c.old, c.new) for c in candidates] == [("phone", "phone_number")]
    assert candidates[0].source == "heuristic"
    asked = find_rename_candidates(changes, [{"kind": "rename_field", "model": "student", "old": "phone", "new": "phone_number"}])
    assert asked[0].source == "django"


def test_plan_fresh_tables_are_not_destructive():
    data = {
        "applied_count": 5,
        "pending": [{"app": "a", "name": "0001", "operations": [op("CreateModel", model="X")]},
                    {"app": "a", "name": "0002", "operations": [op("RemoveField", model="X", field_name="f")]}],
    }
    plan = build_plan(data)
    assert not plan.destructive_operations


def test_plan_missing_database_has_no_data_at_risk():
    data = {"db_missing_file": True, "pending": [
        {"app": "contenttypes", "name": "0002", "operations": [op("RemoveField", model="ContentType", field_name="name")]}]}
    assert build_plan(data).risk is Risk.NONE


def test_plan_destructive_and_strategy():
    data = {"applied_count": 10, "changes": {"students": [{"name": "0002_x", "operations": [
        op("RemoveField", model="Student", field_name="phone", old_field={"class": "CharField"}),
        op("AddField", model="Student", field_name="phone_number", field={"class": "CharField", "null": True}),
    ]}]}}
    plan = build_plan(data)
    assert plan.risk is Risk.HIGH
    assert plan.needs_makemigrations and not plan.needs_migrate
    assert len(plan.destructive_operations) == 1
    assert any("possibly a rename" in s.title for s in plan.strategies)
    assert plan.to_dict()["rename_candidates"][0]["new"] == "phone_number"


def test_plan_blocking_problems():
    plan = build_plan({"conflicts": {"students": ["0002_a", "0002_b"]}})
    assert plan.blocking_problems and not plan.is_up_to_date
