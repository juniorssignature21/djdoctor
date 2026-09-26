"""Static detection of environment variables read by the settings module.

Parses the settings source with :mod:`ast` (never executes it) and recognises:

* ``os.environ["X"]``, ``os.environ.get("X", default)``, ``os.getenv("X")``
* django-environ: ``env("X")``, ``env.bool("X", default=...)``, ``env.db()``,
  and defaults declared in ``environ.Env(X=(bool, False))``
* python-decouple: ``config("X", default=...)``
* dj-database-url: ``dj_database_url.config()`` (reads ``DATABASE_URL``)

Only variable *names* are ever reported, never values.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from django_doctor.project import parse_python

_ENVIRON_TYPED = {
    "str", "bool", "int", "float", "list", "tuple", "dict", "json", "url", "db", "db_url",
    "cache", "cache_url", "email", "email_url", "search_url", "path", "bytes", "decimal",
}


@dataclass
class EnvVarUsage:
    name: str
    file: Path
    line: int
    #: True when a missing variable raises at import time (no default).
    required: bool
    #: True when a default value is provided in code.
    has_default: bool
    via: str
    setting: str | None = None


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_os_environ(node: ast.AST) -> bool:
    # os.environ  |  environ (from os import environ)
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return isinstance(node.value, ast.Name) and node.value.id == "os"
    return isinstance(node, ast.Name) and node.id == "environ"


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path
        self.usages: list[EnvVarUsage] = []
        self.env_names: set[str] = set()  # names bound to environ.Env(...)
        self.env_scheme_defaults: set[str] = set()
        self.config_names: set[str] = set()  # decouple (config imported from it)
        self._setting: str | None = None

    # Track the setting being assigned so reports can say "SECRET_KEY <- X".
    def visit_Assign(self, node: ast.Assign) -> None:
        target = node.targets[0] if node.targets else None
        previous = self._setting
        if isinstance(target, ast.Name) and target.id.isupper():
            self._setting = target.id
        self._record_env_binding(node)
        self.generic_visit(node)
        self._setting = previous

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        previous = self._setting
        if isinstance(node.target, ast.Name) and node.target.id.isupper():
            self._setting = node.target.id
        self.generic_visit(node)
        self._setting = previous

    def _record_env_binding(self, node: ast.Assign) -> None:
        value = node.value
        if not isinstance(value, ast.Call):
            return
        func = value.func
        is_env_ctor = (isinstance(func, ast.Attribute) and func.attr == "Env") or (
            isinstance(func, ast.Name) and func.id == "Env"
        )
        if is_env_ctor:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.env_names.add(target.id)
            for kw in value.keywords:
                if kw.arg and isinstance(kw.value, ast.Tuple) and len(kw.value.elts) >= 2:
                    self.env_scheme_defaults.add(kw.arg)
        is_autoconfig = isinstance(func, ast.Name) and func.id in {"AutoConfig", "Config"}
        if is_autoconfig:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.config_names.add(target.id)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "decouple":
            for alias in node.names:
                if alias.name == "config":
                    self.config_names.add(alias.asname or "config")
        self.generic_visit(node)

    def _add(self, name: str | None, node: ast.AST, *, required: bool, has_default: bool, via: str) -> None:
        if not name:
            return
        self.usages.append(
            EnvVarUsage(name, self.path, getattr(node, "lineno", 0), required, has_default, via, self._setting)
        )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_os_environ(node.value) and isinstance(node.ctx, ast.Load):
            key = node.slice
            if isinstance(key, ast.Index):  # pragma: no cover - Python < 3.9
                key = key.value  # type: ignore[attr-defined]
            self._add(_const_str(key), node, required=True, has_default=False, via="os.environ[...]")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        first = _const_str(node.args[0]) if node.args else None
        kw = {k.arg for k in node.keywords if k.arg}
        if isinstance(func, ast.Attribute):
            owner = func.value
            if func.attr == "get" and _is_os_environ(owner):
                has_default = len(node.args) > 1 or "default" in kw
                self._add(first, node, required=False, has_default=has_default, via="os.environ.get")
            elif func.attr == "getenv" and isinstance(owner, ast.Name) and owner.id == "os":
                has_default = len(node.args) > 1 or "default" in kw
                self._add(first, node, required=False, has_default=has_default, via="os.getenv")
            elif isinstance(owner, ast.Name) and owner.id in self.env_names and func.attr in _ENVIRON_TYPED:
                name = first
                if name is None and func.attr in {"db", "db_url"}:
                    name = "DATABASE_URL"
                elif name is None and func.attr in {"cache", "cache_url"}:
                    name = "CACHE_URL"
                elif name is None and func.attr in {"email", "email_url"}:
                    name = "EMAIL_URL"
                has_default = len(node.args) > 1 or "default" in kw or (name in self.env_scheme_defaults)
                self._add(name, node, required=not has_default, has_default=has_default, via=f"env.{func.attr}")
            elif func.attr == "config" and isinstance(owner, ast.Name) and owner.id == "dj_database_url":
                has_default = "default" in kw
                self._add("DATABASE_URL", node, required=not has_default, has_default=has_default,
                          via="dj_database_url.config")
        elif isinstance(func, ast.Name):
            if func.id == "getenv":
                has_default = len(node.args) > 1 or "default" in kw
                self._add(first, node, required=False, has_default=has_default, via="getenv")
            elif func.id in self.env_names:
                has_default = "default" in kw or (first in self.env_scheme_defaults)
                self._add(first, node, required=not has_default, has_default=has_default, via="env()")
            elif func.id in self.config_names:
                has_default = "default" in kw
                self._add(first, node, required=not has_default, has_default=has_default, via="config()")
        self.generic_visit(node)


def scan_env_usage(files: list[Path]) -> list[EnvVarUsage]:
    usages: list[EnvVarUsage] = []
    for path in files:
        tree = parse_python(path)
        if tree is None:
            continue
        visitor = _Visitor(path)
        visitor.visit(tree)
        usages.extend(visitor.usages)
    return usages


def loads_dotenv(files: list[Path]) -> bool:
    """Whether the settings appear to load a .env file themselves."""
    markers = ("load_dotenv", "read_env", "dotenv_values", "decouple", "AutoConfig")
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(m in text for m in markers):
            return True
    return False
