"""Run ``manage.py`` commands in the project's interpreter.

Three modes:

* :func:`run_captured` — collect output (for commands whose output we parse).
* :func:`run_streaming` — stream output live to the terminal while teeing it
  into a :class:`TracebackRecorder`, so a failure can be explained afterwards
  and saved as the "last error" for ``djdoctor explain``.
* :func:`run_interactive` — hand the terminal over completely (``shell``).
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from django_doctor.project import DjangoProject

_TRACEBACK_START = "Traceback (most recent call last):"


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    captured_error: str | None = None
    interrupted: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


_CHAIN_MARKERS = (
    "The above exception was the direct cause of the following exception:",
    "During handling of the above exception, another exception occurred:",
)
_MAX_TAIL_AFTER_EXCEPTION = 25


class TracebackRecorder:
    """Keep a rolling window of output and remember the most recent traceback.

    Handles chained tracebacks and multi-line exception messages (PostgreSQL
    ``DETAIL:``/``HINT:`` lines). ``on_error`` is called once per completed
    traceback with its text.
    """

    def __init__(self, on_error: Callable[[str], None] | None = None, max_lines: int = 600):
        self.recent: deque[str] = deque(maxlen=max_lines)
        self.on_error = on_error
        self.last_error: str | None = None
        self._current: list[str] | None = None
        self._exc_index: int | None = None
        self._lock = threading.Lock()

    def feed(self, line: str) -> None:
        from django_doctor.explain.traceback import looks_like_exception_line

        finished = None
        with self._lock:
            text = line.rstrip("\n").rstrip("\r")
            self.recent.append(text)
            bare = text.strip()
            if bare == _TRACEBACK_START:
                if self._current is not None and self._last_nonblank() in _CHAIN_MARKERS:
                    self._current.append(text)
                    self._exc_index = None
                    return
                finished = self._finish()
                self._current = [text]
                self._exc_index = None
                # Keep the request line Django logs just before a traceback.
                previous = list(self.recent)[-2] if len(self.recent) > 1 else ""
                if previous.startswith(("Internal Server Error:", "Bad Request:", "Forbidden")):
                    self._current.insert(0, previous)
            elif self._current is not None:
                indented = text.startswith((" ", "\t"))
                if self._exc_index is None:
                    self._current.append(text)
                    if bare and not indented and len(self._current) > 2 and looks_like_exception_line(bare):
                        self._exc_index = len(self._current) - 1
                        self.last_error = "\n".join(self._current)
                elif bare in _CHAIN_MARKERS:
                    self._current.append(text)
                    self._exc_index = None
                elif text.startswith("[") or (len(self._current) - self._exc_index) > _MAX_TAIL_AFTER_EXCEPTION:
                    finished = self._finish()
                else:
                    self._current.append(text)
                    if bare:
                        self.last_error = "\n".join(self._current).rstrip()
                if self._current is not None and len(self._current) > 1500:
                    self._current = self._current[-1500:]
        if finished and self.on_error:
            self.on_error(finished)

    def _last_nonblank(self) -> str:
        for item in reversed(self._current or []):
            if item.strip():
                return item.strip()
        return ""

    def _finish(self) -> str | None:
        done = None
        if self._current is not None and self._exc_index is not None:
            done = "\n".join(self._current).rstrip()
            self.last_error = done
        self._current = None
        self._exc_index = None
        return done

    def close(self) -> None:
        """Flush a traceback still being collected when the stream ends."""
        with self._lock:
            finished = self._finish()
        if finished and self.on_error:
            self.on_error(finished)

    def error_text(self) -> str | None:
        with self._lock:
            if self.last_error:
                return self.last_error
            if self._current:
                return "\n".join(self._current)
            return None

    def tail(self, n: int = 60) -> str:
        with self._lock:
            return "\n".join(list(self.recent)[-n:])


def _base_cmd(project: DjangoProject, args: Sequence[str]) -> list[str]:
    return [project.python, str(project.manage_py), *args]


def run_captured(project: DjangoProject, args: Sequence[str], *, timeout: int | None = 600,
                 extra_env: dict[str, str] | None = None) -> CommandResult:
    env = project.subprocess_env()
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        _base_cmd(project, args), cwd=project.root, env=env, capture_output=True,
        text=True, timeout=timeout, stdin=subprocess.DEVNULL,
    )
    recorder = TracebackRecorder()
    for line in (proc.stderr or "").splitlines():
        recorder.feed(line)
    recorder.close()
    if recorder.error_text() is None and proc.returncode != 0 and proc.stderr:
        # Errors printed without a traceback (e.g. "CommandError: ...").
        recorder.last_error = proc.stderr.strip()[-4000:]
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "", recorder.error_text())


def run_streaming(
    project: DjangoProject,
    args: Sequence[str],
    *,
    recorder: TracebackRecorder | None = None,
    merge_stdout: bool = False,
    cmd: list[str] | None = None,
    out=None,
    err=None,
) -> CommandResult:
    """Run a command with live output; stderr (and optionally stdout) is teed."""
    recorder = recorder or TracebackRecorder()
    out = out or sys.stdout
    err = err or sys.stderr
    full_cmd = cmd or _base_cmd(project, args)
    proc = subprocess.Popen(
        full_cmd,
        cwd=project.root,
        env=project.subprocess_env(),
        stdout=subprocess.PIPE if merge_stdout else None,
        stderr=subprocess.STDOUT if merge_stdout else subprocess.PIPE,
        text=True,
        bufsize=1,
        errors="replace",
    )
    stream = proc.stdout if merge_stdout else proc.stderr
    sink = out if merge_stdout else err
    captured: list[str] = []

    def pump() -> None:
        assert stream is not None
        for line in stream:
            sink.write(line)
            sink.flush()
            captured.append(line)
            recorder.feed(line)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    interrupted = False
    try:
        returncode = proc.wait()
    except KeyboardInterrupt:
        interrupted = True
        try:
            proc.send_signal(signal.SIGINT)
            returncode = proc.wait(timeout=10)
        except Exception:
            proc.kill()
            returncode = proc.wait()
    thread.join(timeout=5)
    recorder.close()
    text = "".join(captured)
    if recorder.error_text() is None and returncode not in (0, 130) and not interrupted:
        recorder.last_error = recorder.tail(40) or None
    return CommandResult(
        returncode=returncode,
        stdout=text if merge_stdout else "",
        stderr="" if merge_stdout else text,
        captured_error=recorder.error_text(),
        interrupted=interrupted,
    )


def run_interactive(project: DjangoProject, args: Sequence[str]) -> CommandResult:
    try:
        proc = subprocess.run(_base_cmd(project, args), cwd=project.root, env=project.subprocess_env())
        return CommandResult(proc.returncode)
    except KeyboardInterrupt:
        return CommandResult(130, interrupted=True)


@dataclass
class MigrationSnapshot:
    files: dict[Path, float] = field(default_factory=dict)

    @classmethod
    def take(cls, directories: Sequence[Path]) -> MigrationSnapshot:
        files = {}
        for d in directories:
            if d.is_dir():
                for f in d.glob("*.py"):
                    if f.name != "__init__.py":
                        files[f] = f.stat().st_mtime
        return cls(files)

    def new_files(self, after: MigrationSnapshot) -> list[Path]:
        return sorted(p for p in after.files if p not in self.files)
