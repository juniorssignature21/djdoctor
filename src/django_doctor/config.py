"""User configuration: ``[tool.djdoctor]`` in pyproject.toml or ``.djdoctor.toml``.

Example::

    [tool.djdoctor]
    settings = "mysite.settings.dev"
    python = ".venv/bin/python"
    env_file = ".env"
    skip_checks = ["security.missing_env"]

    [tool.djdoctor.ai]
    provider = "anthropic"
    model = "..."
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django_doctor.branding import CONFIG_FILENAME, CONFIG_SECTION
from django_doctor.exceptions import ConfigurationError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass
class AIConfig:
    provider: str | None = None
    model: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class DoctorConfig:
    settings: str | None = None
    python: str | None = None
    manage_py: str | None = None
    env_file: str | None = None
    skip_checks: list[str] = field(default_factory=list)
    ai: AIConfig = field(default_factory=AIConfig)
    source: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: Path | None = None) -> DoctorConfig:
        known = {"settings", "python", "manage_py", "env_file", "skip_checks", "ai"}
        unknown = set(data) - known
        if unknown:
            raise ConfigurationError(
                f"Unknown option(s) in {source}: {', '.join(sorted(unknown))}",
                hint=f"Supported options: {', '.join(sorted(known))}",
            )
        ai_data = dict(data.get("ai") or {})
        ai = AIConfig(
            provider=ai_data.pop("provider", None),
            model=ai_data.pop("model", None),
            options=ai_data,
        )
        skip = data.get("skip_checks") or []
        if not isinstance(skip, list):
            raise ConfigurationError(f"'skip_checks' in {source} must be a list of check ids.")
        return cls(
            settings=data.get("settings"),
            python=data.get("python"),
            manage_py=data.get("manage_py"),
            env_file=data.get("env_file"),
            skip_checks=[str(s) for s in skip],
            ai=ai,
            source=source,
        )


def load_config(root: Path) -> DoctorConfig:
    """Load configuration from ``root``. Missing files yield defaults."""
    standalone = root / CONFIG_FILENAME
    if standalone.is_file():
        data = _read_toml(standalone)
        return DoctorConfig.from_dict(data.get("tool", {}).get(CONFIG_SECTION, data), standalone)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        data = _read_toml(pyproject)
        section = data.get("tool", {}).get(CONFIG_SECTION)
        if section is not None:
            return DoctorConfig.from_dict(section, pyproject)
    return DoctorConfig()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"Could not parse {path}: {exc}") from exc
