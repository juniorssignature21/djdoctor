"""Django project detection.

Everything here is *static*: it inspects files on disk and never imports the
user's code. Anything that needs Django loaded goes through
:mod:`django_doctor.bridge`.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from django_doctor.branding import ENV_PREFIX, STATE_DIRNAME
from django_doctor.config import DoctorConfig, load_config
from django_doctor.exceptions import InterpreterError, ProjectNotFoundError

#: Directories never worth descending into when scanning a project.
IGNORED_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        ".tox", ".nox", ".venv", "venv", "env", ".env", "virtualenv", "node_modules",
        "site-packages", "dist", "build", ".eggs", "htmlcov", STATE_DIRNAME, ".idea", ".vscode",
    }
)

_SETTINGS_RE = re.compile(
    r"""DJANGO_SETTINGS_MODULE['"]\s*,\s*['"](?P<module>[\w.]+)['"]"""
)


@dataclass
class SettingsSource:
    """Where the settings module came from, for transparent reporting."""

    module: str
    origin: str  # "--settings", "environment", "config", "manage.py", "heuristic"


@dataclass
class DjangoProject:
    root: Path
    manage_py: Path
    settings: SettingsSource | None
    python: str
    config: DoctorConfig = field(default_factory=DoctorConfig)
    notes: list[str] = field(default_factory=list)

    # ----------------------------------------------------------- derived paths
    @property
    def settings_module(self) -> str | None:
        return self.settings.module if self.settings else None

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIRNAME

    def ensure_state_dir(self) -> Path:
        self.state_dir.mkdir(exist_ok=True)
        gitignore = self.state_dir / ".gitignore"
        if not gitignore.exists():
            # Keep local state (error logs, DB backups) out of version control.
            gitignore.write_text("*\n", encoding="utf-8")
        return self.state_dir

    @property
    def last_error_log(self) -> Path:
        return self.state_dir / "last_error.log"

    def settings_files(self) -> list[Path]:
        """Python files that make up the settings module (a module or a package)."""
        if not self.settings_module:
            return []
        return module_files(self.root, self.settings_module)

    def dependency_files(self) -> list[Path]:
        names = ["requirements.txt", "requirements/base.txt", "requirements/dev.txt",
                 "requirements-dev.txt", "pyproject.toml", "Pipfile", "setup.cfg"]
        found = [self.root / n for n in names if (self.root / n).is_file()]
        # A repository root one level up often holds pyproject/requirements.
        parent = self.root.parent
        for n in ("requirements.txt", "pyproject.toml"):
            p = parent / n
            if p.is_file() and (parent / ".git").exists():
                found.append(p)
        return found

    def env_files(self) -> list[Path]:
        candidates = []
        if self.config.env_file:
            candidates.append(self.root / self.config.env_file)
        candidates += [self.root / ".env", self.root.parent / ".env"]
        seen: list[Path] = []
        for c in candidates:
            if c.is_file() and c not in seen:
                seen.append(c)
        return seen

    def subprocess_env(self) -> dict[str, str]:
        """Environment for child processes running the project's code."""
        env = dict(os.environ)
        if self.settings_module:
            env["DJANGO_SETTINGS_MODULE"] = self.settings_module
        env["PYTHONUNBUFFERED"] = "1"
        # Make the project importable even when the interpreter is not the one
        # manage.py would add to sys.path automatically.
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(self.root) + (os.pathsep + existing if existing else "")
        if self.config.env_file:
            env.update(read_env_file(self.root / self.config.env_file))
        return env


# --------------------------------------------------------------------- lookup
def find_manage_py(start: Path) -> Path | None:
    """Search ``start`` and its parents for ``manage.py``."""
    start = start.resolve()
    for directory in [start, *start.parents]:
        candidate = directory / "manage.py"
        if candidate.is_file():
            return candidate
    return None


def find_nested_manage_py(start: Path, max_depth: int = 2) -> list[Path]:
    """Look *below* ``start`` for manage.py (used only for a helpful hint)."""
    results: list[Path] = []
    start = start.resolve()
    for dirpath, dirnames, filenames in os.walk(start):
        depth = len(Path(dirpath).relative_to(start).parts)
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        if depth > max_depth:
            dirnames[:] = []
            continue
        if "manage.py" in filenames and depth > 0:
            results.append(Path(dirpath) / "manage.py")
    return sorted(results)


def settings_from_manage_py(manage_py: Path) -> str | None:
    try:
        text = manage_py.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _SETTINGS_RE.search(text)
    return match.group("module") if match else None


def guess_settings_module(root: Path) -> str | None:
    """Fallback: find ``<pkg>/settings.py`` or ``<pkg>/settings/`` next to manage.py."""
    candidates = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in IGNORED_DIRS or child.name.startswith("."):
            continue
        if (child / "settings.py").is_file() or (child / "settings" / "__init__.py").is_file():
            candidates.append(f"{child.name}.settings")
    return candidates[0] if len(candidates) == 1 else None


def module_files(root: Path, module: str) -> list[Path]:
    base = root.joinpath(*module.split("."))
    if base.with_suffix(".py").is_file():
        return [base.with_suffix(".py")]
    if (base / "__init__.py").is_file():
        return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return []


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal ``.env`` parser (KEY=VALUE, optional quotes, ``export`` prefix)."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = value
    return values


def env_file_keys(path: Path) -> set[str]:
    return set(read_env_file(path))


# ----------------------------------------------------------------- interpreter
def resolve_python(explicit: str | None, config: DoctorConfig, root: Path) -> tuple[str, str]:
    """Pick the interpreter that runs the project's code.

    Priority: ``--python`` > ``DJDOCTOR_PYTHON`` > config ``python`` > the
    interpreter running Django Doctor. Returns ``(path, origin)``.
    """
    for value, origin in (
        (explicit, "--python"),
        (os.environ.get(f"{ENV_PREFIX}PYTHON"), f"{ENV_PREFIX}PYTHON"),
        (config.python, "config"),
    ):
        if value:
            candidate = Path(value)
            if not candidate.is_absolute() and os.sep in value:
                candidate = (root / candidate).resolve()
            resolved = str(candidate) if candidate.exists() else shutil.which(value)
            if not resolved:
                raise InterpreterError(
                    f"Python interpreter '{value}' (from {origin}) was not found.",
                    hint="Pass the full path to the python executable of your project's virtualenv.",
                )
            return resolved, origin
    return sys.executable, "current interpreter"


def local_virtualenvs(root: Path) -> list[Path]:
    found = []
    for name in (".venv", "venv", "env"):
        for base in (root, root.parent):
            for exe in ("bin/python", "Scripts/python.exe"):
                p = base / name / exe
                if p.exists():
                    found.append(p)
    return found


# ------------------------------------------------------------------ detection
def detect_project(
    start: Path | None = None,
    *,
    settings: str | None = None,
    python: str | None = None,
) -> DjangoProject:
    start = (start or Path.cwd()).resolve()
    if start.is_file():
        start = start.parent
    if not start.exists():
        raise ProjectNotFoundError(f"The directory {start} does not exist.")

    manage_py = find_manage_py(start)
    if manage_py is None:
        nested = find_nested_manage_py(start)
        hint = "Run this command from your Django project directory, or pass --project PATH."
        if nested:
            rel = ", ".join(str(p.parent.relative_to(start)) for p in nested[:3])
            hint = f"Found manage.py in a subdirectory: {rel}. Run from there or pass --project {nested[0].parent.relative_to(start)}."
        raise ProjectNotFoundError(
            "No Django project detected.",
            detail=(
                "Django Doctor searched the current directory and its parents "
                "but could not find manage.py."
            ),
            hint=hint,
        )

    root = manage_py.parent
    config = load_config(root)
    notes: list[str] = []

    if config.manage_py:
        configured = (root / config.manage_py).resolve()
        if configured.is_file():
            manage_py = configured

    source: SettingsSource | None = None
    from_manage = settings_from_manage_py(manage_py)
    env_value = os.environ.get("DJANGO_SETTINGS_MODULE")
    if settings:
        source = SettingsSource(settings, "--settings")
    elif env_value:
        source = SettingsSource(env_value, "environment")
        if from_manage and from_manage != env_value:
            notes.append(
                f"DJANGO_SETTINGS_MODULE in your environment ({env_value}) overrides "
                f"the default in manage.py ({from_manage})."
            )
    elif config.settings:
        source = SettingsSource(config.settings, "config")
    elif from_manage:
        source = SettingsSource(from_manage, "manage.py")
    else:
        guessed = guess_settings_module(root)
        if guessed:
            source = SettingsSource(guessed, "heuristic")
            notes.append(f"Settings module guessed as {guessed}; pass --settings to be explicit.")

    interpreter, origin = resolve_python(python, config, root)
    if origin == "current interpreter" and not os.environ.get("VIRTUAL_ENV"):
        venvs = local_virtualenvs(root)
        if venvs and Path(sys.prefix).resolve() not in [v.parent.parent.resolve() for v in venvs]:
            notes.append(
                f"A virtualenv exists at {venvs[0].parent.parent} but is not active; "
                f"Django Doctor is using {interpreter}. Activate it or pass --python."
            )

    return DjangoProject(
        root=root, manage_py=manage_py, settings=source, python=interpreter,
        config=config, notes=notes,
    )


# ---------------------------------------------------------- static inspection
def iter_project_files(root: Path, suffixes: tuple[str, ...], *, exclude: set[Path] | None = None):
    """Yield project files with the given suffixes, skipping virtualenvs & co."""
    exclude = {p.resolve() for p in (exclude or set())}
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORED_DIRS
            and not d.startswith(".")
            and (current / d).resolve() not in exclude
            and not (current / d / "pyvenv.cfg").exists()
        ]
        for name in filenames:
            if name.endswith(suffixes):
                yield current / name


def parse_python(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (SyntaxError, ValueError, OSError):
        return None
