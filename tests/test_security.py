from django_doctor.security import MASK, is_sensitive_name, redact_mapping, redact_text


def test_redacts_url_credentials():
    assert redact_text("postgres://user:s3cret@host:5432/db") == f"postgres://user:{MASK}@host:5432/db"


def test_redacts_key_value_pairs():
    text = "PASSWORD='abc123' api_key=XYZ secret_key: \"hello\""
    out = redact_text(text)
    assert "abc123" not in out and "XYZ" not in out and "hello" not in out


def test_redacts_bearer_tokens():
    assert "abcdefghijkl" not in redact_text("Authorization: Bearer abcdefghijkl")


def test_leaves_normal_text():
    assert redact_text("no such column: students_student.phone") == "no such column: students_student.phone"


def test_redact_mapping():
    data = {"SECRET_KEY": "x", "NAME": "db", "nested": {"DATABASE_PASSWORD": "p", "HOST": "h"}, "EMPTY_TOKEN": ""}
    out = redact_mapping(data)
    assert out["SECRET_KEY"] == MASK and out["nested"]["DATABASE_PASSWORD"] == MASK
    assert out["NAME"] == "db" and out["nested"]["HOST"] == "h"
    assert out["EMPTY_TOKEN"] == ""


def test_sensitive_names():
    assert is_sensitive_name("AWS_SECRET_ACCESS_KEY")
    assert is_sensitive_name("STRIPE_API_KEY")
    assert not is_sensitive_name("DEBUG")
