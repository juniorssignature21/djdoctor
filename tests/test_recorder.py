from django_doctor.bridge.manage import TracebackRecorder

SERVER_LOG = """\
Starting development server at http://127.0.0.1:8000/
Internal Server Error: /students/
Traceback (most recent call last):
  File "/app/students/views.py", line 5, in index
    return redirect("dashboard")
django.urls.exceptions.NoReverseMatch: Reverse for 'dashboard' not found.
[26/Sep/2026 08:26:28] "GET /students/ HTTP/1.1" 500 94775
Traceback (most recent call last):
  File "/app/db.py", line 1, in q
    run()
django.db.utils.ProgrammingError: column x.y does not exist
LINE 1: SELECT ...

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/views.py", line 2, in v
    q()
django.db.utils.ProgrammingError: column x.y does not exist
LINE 1: SELECT ...
"""


def test_records_each_traceback_once_with_context():
    seen = []
    recorder = TracebackRecorder(on_error=seen.append)
    for line in SERVER_LOG.splitlines(keepends=True):
        recorder.feed(line)
    recorder.close()
    assert len(seen) == 2
    assert seen[0].startswith("Internal Server Error: /students/")
    assert seen[0].rstrip().endswith("not found.")
    assert "GET /students/" not in seen[0]
    # The chained traceback is kept together, including the multi-line message.
    assert "direct cause" in seen[1] and seen[1].rstrip().endswith("LINE 1: SELECT ...")
    assert recorder.error_text() == seen[1]


def test_no_traceback():
    recorder = TracebackRecorder()
    recorder.feed("all fine\n")
    recorder.close()
    assert recorder.error_text() is None
    assert recorder.tail() == "all fine"
