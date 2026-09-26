"""Template and static file checks."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from django_doctor.diagnostics.context import DoctorContext
from django_doctor.diagnostics.models import CheckResult, Status


def _resolve(ctx: DoctorContext, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else (ctx.project.root / path)


def check_templates(ctx: DoctorContext) -> Iterator[CheckResult]:
    info = ctx.info
    if info is None:
        yield CheckResult("templates.skipped", "Templates", Status.SKIPPED, "Skipped: settings could not be loaded")
        return
    for backend in info.get("templates", []):
        for d in backend.get("dirs", []):
            if not _resolve(ctx, d).exists():  # type: ignore[union-attr]
                yield CheckResult("templates.dir_missing", "Templates", Status.WARNING,
                                  f"Template directory does not exist: {d}", hint="Create it or remove it from TEMPLATES['DIRS'].")
    if not info.get("templates"):
        yield CheckResult("templates.none", "Templates", Status.INFO, "No TEMPLATES backend configured")
        return
    refs = ctx.references
    names = refs.unique("templates")
    result = ctx.probe("templates", {"names": names, "compile_project_templates": True})
    if not result.ok:
        yield CheckResult("templates.error", "Templates", Status.ERROR, "Templates could not be checked",
                          [result.error_message], diagnosis=ctx.explain(result))
        return
    data = result.data
    missing = data.get("missing", [])
    if missing:
        details = [f"'{n}' used at {', '.join(refs.where('templates', n, ctx.project.root))}" for n in missing[:15]]
        yield CheckResult("templates.missing", "Templates", Status.ERROR,
                          f"{len(missing)} referenced template(s) do not exist (TemplateDoesNotExist at runtime)", details,
                          hint="Check the file path relative to a templates/ folder and TEMPLATES settings.")
    for err in data.get("syntax_errors", [])[:15]:
        yield CheckResult("templates.syntax", "Templates", Status.ERROR, f"Template syntax error in {err['template']}",
                          [err["message"]])
    if not missing and not data.get("syntax_errors"):
        yield CheckResult("templates.ok", "Templates", Status.OK,
                          f"{len(names)} referenced template(s) found; project templates compile")


def check_static(ctx: DoctorContext) -> Iterator[CheckResult]:
    info = ctx.info
    if info is None:
        yield CheckResult("static.skipped", "Static files", Status.SKIPPED, "Skipped: settings could not be loaded")
        return
    static = info.get("static") or {}
    media = info.get("media") or {}
    debug = info.get("debug")
    problems = 0
    if not static.get("url"):
        problems += 1
        yield CheckResult("static.url", "Static files", Status.ERROR, "STATIC_URL is not set", hint="Set STATIC_URL = 'static/'.")
    root = _resolve(ctx, static.get("root"))
    dirs = [_resolve(ctx, d) for d in static.get("dirs", [])]
    for d in dirs:
        if d is not None and not d.exists():
            problems += 1
            yield CheckResult("static.dir_missing", "Static files", Status.WARNING, f"STATICFILES_DIRS entry does not exist: {d}")
    if root and any(d and d.resolve() == root.resolve() for d in dirs):
        problems += 1
        yield CheckResult("static.root_in_dirs", "Static files", Status.ERROR, "STATIC_ROOT is also listed in STATICFILES_DIRS",
                          hint="collectstatic would copy files onto themselves; use a separate folder.")
    if not root and not debug:
        problems += 1
        yield CheckResult("static.root", "Static files", Status.WARNING, "STATIC_ROOT is not set while DEBUG is False",
                          hint="collectstatic needs STATIC_ROOT; set STATIC_ROOT = BASE_DIR / 'staticfiles'.")
    if media.get("url") and static.get("url") and media["url"] == static["url"]:
        problems += 1
        yield CheckResult("static.media_url", "Static files", Status.ERROR, "MEDIA_URL and STATIC_URL are the same")
    media_root = _resolve(ctx, media.get("root"))
    if media_root and root and media_root.resolve() == root.resolve():
        problems += 1
        yield CheckResult("static.media_root", "Static files", Status.ERROR, "MEDIA_ROOT and STATIC_ROOT are the same folder",
                          hint="User uploads would be mixed with (and possibly overwritten by) collected static files.")

    refs = ctx.references
    paths = refs.unique("static_paths")
    if paths:
        result = ctx.probe("static", {"paths": paths})
        if result.ok and result.data.get("installed"):
            missing = result.data.get("missing", [])
            if missing:
                problems += 1
                details = [f"'{p}' used at {', '.join(refs.where('static_paths', p, ctx.project.root))}" for p in missing[:15]]
                yield CheckResult("static.missing", "Static files", Status.WARNING,
                                  f"{len(missing)} static file(s) referenced with {{% static %}} were not found", details)
    if not problems:
        yield CheckResult("static.ok", "Static files", Status.OK,
                          f"Static files configured (STATIC_URL={static.get('url')!r}" + (f", {len(paths)} references found)" if paths else ")"))
