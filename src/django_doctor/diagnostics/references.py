"""Static scan of the project's own code for template names, URL names and
static file paths that it references as string literals.

Dynamic values are ignored; only literals are checked, so every reported
problem corresponds to a concrete line the developer can open.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from django_doctor.project import iter_project_files, parse_python

_TEMPLATE_FUNCS = {"render", "render_to_string", "get_template", "select_template", "TemplateResponse",
                   "SimpleTemplateResponse", "render_to_response"}
_URL_FUNCS = {"reverse", "reverse_lazy", "redirect", "resolve_url"}
_TEMPLATE_SUFFIXES = (".html", ".htm", ".txt", ".xml", ".email", ".jinja", ".jinja2", ".j2")

_TPL_URL = re.compile(r"{%\s*url\s+(['\"])(?P<name>[^'\"]+)\1(?P<rest>[^%]*)%}")
_TPL_REF = re.compile(r"{%\s*(?P<tag>extends|include)\s+(['\"])(?P<name>[^'\"]+)\2")
_TPL_STATIC = re.compile(r"{%\s*static\s+(['\"])(?P<path>[^'\"]+)\1")
_URL_NAME = re.compile(r"^[A-Za-z_][\w\-]*(:[A-Za-z_][\w\-]*)*$")


@dataclass
class Reference:
    value: str
    file: Path
    line: int


@dataclass
class ProjectReferences:
    templates: list[Reference] = field(default_factory=list)
    url_names: list[Reference] = field(default_factory=list)
    static_paths: list[Reference] = field(default_factory=list)
    template_files: list[Path] = field(default_factory=list)

    def unique(self, kind: str) -> list[str]:
        return sorted({r.value for r in getattr(self, kind)})

    def where(self, kind: str, value: str, root: Path, limit: int = 3) -> list[str]:
        refs = [r for r in getattr(self, kind) if r.value == value][:limit]
        out = []
        for r in refs:
            try:
                rel = r.file.relative_to(root)
            except ValueError:
                rel = r.file
            out.append(f"{rel}:{r.line}")
        return out


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _scan_python(path: Path, refs: ProjectReferences) -> None:
    tree = parse_python(path)
    if tree is None:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in _TEMPLATE_FUNCS:
                # render(request, "t.html") has the template second.
                idx = 1 if name in {"render", "TemplateResponse", "SimpleTemplateResponse"} else 0
                arg = node.args[idx] if len(node.args) > idx else None
                for kw in node.keywords:
                    if kw.arg in ("template_name", "template"):
                        arg = kw.value
                values = [_str(arg)]
                if isinstance(arg, (ast.List, ast.Tuple)) and name == "select_template":
                    values = [_str(e) for e in arg.elts]
                for v in values:
                    if v:
                        refs.templates.append(Reference(v, path, node.lineno))
            elif name in _URL_FUNCS and node.args:
                value = _str(node.args[0])
                if value and _URL_NAME.match(value) and "." not in value:
                    refs.url_names.append(Reference(value, path, node.lineno))
            for kw in node.keywords:
                if kw.arg == "template_name" and name not in _TEMPLATE_FUNCS and _str(kw.value):
                    refs.templates.append(Reference(_str(kw.value), path, node.lineno))  # type: ignore[arg-type]
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    target = stmt.targets[0]
                    if isinstance(target, ast.Name) and target.id == "template_name" and _str(stmt.value):
                        refs.templates.append(Reference(_str(stmt.value), path, stmt.lineno))  # type: ignore[arg-type]


def _scan_template(path: Path, refs: ProjectReferences) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if "{%" not in text:
        return
    for m in _TPL_URL.finditer(text):
        if re.search(r"\bas\s+\w+\s*$", m.group("rest").strip()) or " as " in f" {m.group('rest')} ":
            continue  # {% url 'x' as var %} does not raise when missing
        refs.url_names.append(Reference(m.group("name"), path, text.count("\n", 0, m.start()) + 1))
    for m in _TPL_REF.finditer(text):
        refs.templates.append(Reference(m.group("name"), path, text.count("\n", 0, m.start()) + 1))
    for m in _TPL_STATIC.finditer(text):
        refs.static_paths.append(Reference(m.group("path"), path, text.count("\n", 0, m.start()) + 1))


def scan_references(root: Path, *, exclude: set[Path] | None = None) -> ProjectReferences:
    refs = ProjectReferences()
    for path in iter_project_files(root, (".py",), exclude=exclude):
        if "migrations" in path.parts:
            continue
        _scan_python(path, refs)
    for path in iter_project_files(root, _TEMPLATE_SUFFIXES, exclude=exclude):
        if "templates" in path.parts or path.suffix in (".html", ".htm"):
            refs.template_files.append(path)
            _scan_template(path, refs)
    return refs
