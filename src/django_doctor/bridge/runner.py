"""Run the inspection probe in the project's interpreter and decode its result."""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django_doctor.exceptions import ProbeError
from django_doctor.project import DjangoProject

PROBE_PATH = Path(__file__).with_name("probe.py")
DEFAULT_TIMEOUT = 120


@dataclass
class ProbeResult:
    """Outcome of one probe action.

    ``ok`` is False when the *project* failed (settings import error, app
    registry error...). In that case ``stage`` and ``error`` describe it and
    are meant to be fed to the explanation engine.
    """

    action: str
    ok: bool
    data: dict[str, Any]
    stage: str | None = None
    error: dict[str, Any] | None = None
    stderr: str = ""

    @property
    def error_type(self) -> str | None:
        return (self.error or {}).get("type")

    @property
    def error_message(self) -> str:
        return (self.error or {}).get("message", "")


class ProbeRunner:
    def __init__(self, project: DjangoProject, *, timeout: int = DEFAULT_TIMEOUT, debug: bool = False):
        self.project = project
        self.timeout = timeout
        self.debug = debug
        self._cache: dict[str, ProbeResult] = {}

    def run(self, action: str, args: dict[str, Any] | None = None, *, use_cache: bool = True) -> ProbeResult:
        cache_key = action + json.dumps(args or {}, sort_keys=True)
        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]
        with tempfile.TemporaryDirectory(prefix="djdoctor-") as tmp:
            in_path = Path(tmp) / "input.json"
            out_path = Path(tmp) / "output.json"
            in_path.write_text(json.dumps(args or {}), encoding="utf-8")
            cmd = [self.project.python, str(PROBE_PATH), action, "--input", str(in_path), "--output", str(out_path)]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=self.project.root,
                    env=self.project.subprocess_env(),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    stdin=subprocess.DEVNULL,
                )
            except FileNotFoundError as exc:
                raise ProbeError(
                    f"Could not start the Python interpreter {self.project.python}.",
                    hint="Pass --python with the interpreter of your project's virtualenv.",
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise ProbeError(
                    f"Inspecting the project ({action}) timed out after {self.timeout}s.",
                    detail="Your settings or app code may be blocking (e.g. waiting for a network service).",
                ) from exc

            if not out_path.exists():
                raise ProbeError(
                    f"The inspection process exited unexpectedly (code {proc.returncode}).",
                    detail=(proc.stderr or "").strip()[-3000:] or None,
                    hint="Run with --debug for more detail.",
                )
            try:
                payload = json.loads(out_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ProbeError("The inspection process returned invalid data.") from exc

        result = ProbeResult(
            action=action,
            ok=bool(payload.get("ok")),
            data=payload.get("data") or {},
            stage=payload.get("stage"),
            error=payload.get("error"),
            stderr=proc.stderr or "",
        )
        self._cache[cache_key] = result
        return result

    def clear_cache(self) -> None:
        self._cache.clear()
