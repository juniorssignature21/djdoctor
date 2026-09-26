from django_doctor.explain.traceback import looks_like_exception_line, parse_error

CHAINED = '''\
Internal Server Error: /students/
Traceback (most recent call last):
  File "/venv/lib/python3.11/site-packages/django/db/backends/utils.py", line 105, in _execute
    return self.cursor.execute(sql, params)
psycopg2.errors.UndefinedColumn: column students_student.phone_number does not exist
LINE 1: SELECT "students_student"."phone_number" FROM ...
                                    ^

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/students/views.py", line 10, in index
    for s in Student.objects.all():
    ^^^^^^^^
django.db.utils.ProgrammingError: column students_student.phone_number does not exist
LINE 1: SELECT "students_student"."phone_number" FROM ...
[26/Sep/2026 10:00:00] "GET /students/ HTTP/1.1" 500 145
'''


def test_parses_chained_traceback_and_request_path():
    parsed = parse_error(CHAINED)
    assert parsed is not None
    assert [e.type for e in parsed.exceptions] == [
        "psycopg2.errors.UndefinedColumn", "django.db.utils.ProgrammingError"]
    assert parsed.final.short_type == "ProgrammingError"
    assert parsed.final.first_line == "column students_student.phone_number does not exist"
    assert "LINE 1" in parsed.final.message
    assert "GET /students/" not in parsed.final.message  # access log line is not part of the message
    assert parsed.request_path == "/students/"
    frame = parsed.project_frame()
    assert frame.file == "/app/students/views.py" and frame.line == 10
    assert frame.code == "for s in Student.objects.all():"


def test_latest_of_several_tracebacks_wins():
    text = ("Traceback (most recent call last):\n  File \"a.py\", line 1, in x\n    y\nKeyError: 'A'\n\n"
            "some log line\n"
            "Traceback (most recent call last):\n  File \"b.py\", line 2, in z\n    w\nValueError: second\n")
    assert parse_error(text).final.type == "ValueError"


def test_syntax_error_frame_without_function():
    text = '''Traceback (most recent call last):
  File "/app/manage.py", line 22, in <module>
    main()
  File "/app/students/models.py", line 5
    phone = models.CharField(max_length=20
                            ^
SyntaxError: '(' was never closed
'''
    parsed = parse_error(text)
    assert parsed.final.type == "SyntaxError"
    assert parsed.final.frames[-1].function is None
    assert parsed.final.frames[-1].line == 5


def test_error_without_traceback():
    parsed = parse_error("CommandError: Conflicting migrations detected; multiple leaf nodes (0002_a, 0002_b in students).")
    assert parsed.final.type == "CommandError"
    assert parsed.had_traceback is False


def test_pytest_and_docker_prefixes_are_stripped():
    pytest_out = "E   django.db.utils.OperationalError: no such table: students_student"
    assert parse_error(pytest_out).final.short_type == "OperationalError"
    docker = ("web_1  | Traceback (most recent call last):\n"
              "web_1  |   File \"/app/x.py\", line 1, in f\n"
              "web_1  |     g()\n"
              "web_1  | KeyError: 'SECRET_KEY'\n")
    parsed = parse_error(docker)
    assert parsed.final.type == "KeyError" and parsed.final.frames[0].file == "/app/x.py"


def test_ansi_colours_are_removed():
    assert parse_error("\x1b[31mValueError: boom\x1b[0m").final.message == "boom"


def test_no_error_returns_none():
    assert parse_error("Watching for file changes with StatReloader\nall good") is None
    assert parse_error("") is None


def test_known_django_warning_without_type():
    parsed = parse_error("You have 3 unapplied migration(s). Your project may not work properly until you apply the migrations for app(s): students.")
    assert parsed is not None and "unapplied" in parsed.final.message


def test_exception_line_detection():
    assert looks_like_exception_line("django.urls.exceptions.NoReverseMatch: Reverse for 'x' not found.")
    assert looks_like_exception_line("KeyError: 'X'")
    assert not looks_like_exception_line("Starting development server at http://127.0.0.1:8000/")
    assert not looks_like_exception_line("Note: something")
