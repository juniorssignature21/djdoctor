"""Settings, environment variables and CSRF/CORS configuration checks."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlparse

from django_doctor.diagnostics.context import DoctorContext
from django_doctor.diagnostics.envvars import loads_dotenv, scan_env_usage
from django_doctor.diagnostics.models import CheckResult, Status
from django_doctor.project import env_file_keys


def check_settings(ctx: DoctorContext) -> Iterator[CheckResult]:
    result = ctx.info_result
    if not result.ok:
        diagnosis = ctx.explain(result)
        stage = {"django-import": "Django could not be imported", "settings": "The settings module failed to import",
                 "apps": "INSTALLED_APPS could not be loaded"}.get(result.stage or "", "Django could not be set up")
        yield CheckResult("settings.load", "Settings", Status.ERROR, stage,
                          [f"{result.error_type}: {(result.error_message or '').splitlines()[0] if result.error_message else ''}"],
                          diagnosis=diagnosis)
        return
    info = ctx.info or {}
    debug = info.get("debug")
    yield CheckResult("settings.load", "Settings", Status.OK, f"Settings loaded ({info.get('settings_module')})",
                      [f"DEBUG = {debug}", f"{len(info.get('installed_app_names', []))} installed apps"])

    secret = info.get("secret_key") or {}
    if not secret.get("set"):
        yield CheckResult("settings.secret_key", "Settings", Status.ERROR, "SECRET_KEY is empty")
    elif not debug and secret.get("insecure_prefix"):
        yield CheckResult("settings.secret_key", "Settings", Status.ERROR,
                          "DEBUG is False but SECRET_KEY is the insecure development key (django-insecure-...)",
                          hint="Load a strong, secret key from the environment in production.")
    elif not debug and (secret.get("length", 0) < 50 or secret.get("unique_characters", 0) < 5):
        yield CheckResult("settings.secret_key", "Settings", Status.WARNING, "SECRET_KEY looks weak (short or low-entropy)",
                          hint="Use at least 50 random characters.")
    else:
        yield CheckResult("settings.secret_key", "Settings", Status.OK, "SECRET_KEY is set (value not shown)")

    hosts = info.get("allowed_hosts") or []
    if not debug and not hosts:
        yield CheckResult("settings.allowed_hosts", "Settings", Status.ERROR,
                          "ALLOWED_HOSTS is empty while DEBUG is False — every request will be rejected (400)",
                          hint="Add your domain(s), e.g. ALLOWED_HOSTS = ['example.com'].")
    elif not debug and "*" in hosts:
        yield CheckResult("settings.allowed_hosts", "Settings", Status.WARNING,
                          "ALLOWED_HOSTS contains '*' while DEBUG is False",
                          hint="List explicit host names to prevent Host header attacks.")
    else:
        yield CheckResult("settings.allowed_hosts", "Settings", Status.OK,
                          f"ALLOWED_HOSTS = {hosts!r}" if hosts else "ALLOWED_HOSTS is empty (fine while DEBUG is True)")

    names = info.get("installed_app_names") or []
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        yield CheckResult("settings.installed_apps", "Settings", Status.ERROR, "Duplicate entries in INSTALLED_APPS", dupes)
    else:
        yield CheckResult("settings.installed_apps", "Settings", Status.OK, "INSTALLED_APPS entries are importable and unique")


def check_environment(ctx: DoctorContext) -> Iterator[CheckResult]:
    files = ctx.project.settings_files()
    if not files:
        yield CheckResult("environment.scan", "Environment", Status.SKIPPED, "Settings source not found; environment variables not scanned")
        return
    usages = scan_env_usage(files)
    if not usages:
        yield CheckResult("environment.none", "Environment", Status.OK, "Settings do not read environment variables")
        return
    env_files = ctx.project.env_files()
    dotenv_keys: set[str] = set()
    for f in env_files:
        dotenv_keys |= env_file_keys(f)
    dotenv_loaded = loads_dotenv(files) or bool(ctx.project.config.env_file)
    process_env = ctx.project.subprocess_env()

    by_name: dict[str, list] = {}
    for u in usages:
        by_name.setdefault(u.name, []).append(u)

    missing_required, missing_optional, only_in_unloaded_dotenv = [], [], []
    for name, uses in sorted(by_name.items()):
        if name == "DJANGO_SETTINGS_MODULE" or name in process_env:
            continue
        if name in dotenv_keys:
            if not dotenv_loaded:
                only_in_unloaded_dotenv.append(name)
            continue
        required = any(u.required for u in uses)
        no_default = all(not u.has_default for u in uses)
        where = f"{uses[0].file.relative_to(ctx.project.root)}:{uses[0].line}"
        label = f"{name} ({where}{', used for ' + uses[0].setting if uses[0].setting else ''})"
        if required:
            missing_required.append(label)
        elif no_default:
            missing_optional.append(label)

    if missing_required:
        yield CheckResult("environment.missing_required", "Environment", Status.ERROR,
                          f"{len(missing_required)} required environment variable(s) are not set", missing_required,
                          hint="Set them in your shell or .env file. Values are never displayed by Django Doctor.")
    if only_in_unloaded_dotenv:
        yield CheckResult("environment.dotenv_not_loaded", "Environment", Status.WARNING,
                          "Variables are defined in .env but nothing loads that file", only_in_unloaded_dotenv,
                          hint="Load it in settings (python-dotenv / django-environ) or set env_file in [tool.djdoctor].")
    if missing_optional:
        yield CheckResult("environment.missing_optional", "Environment", Status.WARNING,
                          f"{len(missing_optional)} environment variable(s) are not set and have no default (they will be None)",
                          missing_optional)
    if not (missing_required or missing_optional or only_in_unloaded_dotenv):
        yield CheckResult("environment.ok", "Environment", Status.OK,
                          f"All {len(by_name)} environment variables read by settings are available or have defaults")


def check_security(ctx: DoctorContext) -> Iterator[CheckResult]:
    info = ctx.info
    if info is None:
        return
    other = info.get("other") or {}
    middleware = info.get("middleware") or []
    apps = info.get("installed_app_names") or []
    debug = info.get("debug")

    # --- CSRF
    origins = other.get("CSRF_TRUSTED_ORIGINS") or []
    bad = [o for o in origins if "://" not in str(o)]
    if bad:
        yield CheckResult("security.csrf_trusted_origins", "Security", Status.ERROR,
                          "CSRF_TRUSTED_ORIGINS entries must include the scheme (Django 4.0+)", [repr(b) for b in bad],
                          hint="Use 'https://example.com' instead of 'example.com'.")
    if "django.middleware.csrf.CsrfViewMiddleware" not in middleware:
        yield CheckResult("security.csrf_middleware", "Security", Status.WARNING, "CsrfViewMiddleware is not enabled",
                          hint="Without it, forms are not protected against CSRF attacks.")
    elif not bad:
        yield CheckResult("security.csrf", "Security", Status.OK, "CSRF protection configured")

    # --- CORS (django-cors-headers)
    cors_settings = {k: v for k, v in other.items() if k.startswith("CORS_")}
    has_cors_app = "corsheaders" in apps or any(a.startswith("corsheaders.") for a in apps)
    if has_cors_app:
        cors_mw = "corsheaders.middleware.CorsMiddleware"
        common = "django.middleware.common.CommonMiddleware"
        if cors_mw not in middleware:
            yield CheckResult("security.cors_middleware", "Security", Status.ERROR,
                              "corsheaders is installed but CorsMiddleware is not in MIDDLEWARE",
                              hint=f"Add '{cors_mw}' near the top of MIDDLEWARE.")
        elif common in middleware and middleware.index(cors_mw) > middleware.index(common):
            yield CheckResult("security.cors_order", "Security", Status.WARNING,
                              "CorsMiddleware is placed after CommonMiddleware",
                              hint="Place CorsMiddleware before CommonMiddleware so CORS headers are added to all responses.")
        allow_all = cors_settings.get("CORS_ALLOW_ALL_ORIGINS") or cors_settings.get("CORS_ORIGIN_ALLOW_ALL")
        if allow_all and not debug:
            yield CheckResult("security.cors_allow_all", "Security", Status.WARNING,
                              "CORS allows all origins while DEBUG is False",
                              hint="List trusted origins in CORS_ALLOWED_ORIGINS instead.")
        if allow_all and cors_settings.get("CORS_ALLOW_CREDENTIALS"):
            yield CheckResult("security.cors_credentials", "Security", Status.WARNING,
                              "CORS allows all origins together with credentials",
                              hint="Any website could make authenticated requests; restrict the origins.")
        for origin in cors_settings.get("CORS_ALLOWED_ORIGINS") or []:
            parsed = urlparse(str(origin))
            if not parsed.scheme or parsed.path not in ("", "/"):
                yield CheckResult("security.cors_origin_format", "Security", Status.ERROR,
                                  f"Invalid CORS_ALLOWED_ORIGINS entry {origin!r}",
                                  hint="Use scheme://host[:port] without a path, e.g. 'http://localhost:3000'.")
        yield CheckResult("security.cors", "Security", Status.OK, "django-cors-headers configured")
    elif cors_settings:
        yield CheckResult("security.cors_unused", "Security", Status.WARNING,
                          f"{', '.join(sorted(cors_settings))} set, but 'corsheaders' is not in INSTALLED_APPS",
                          hint="Install django-cors-headers and add it to INSTALLED_APPS, or remove the settings.")

    if not debug:
        weak = []
        if not other.get("SESSION_COOKIE_SECURE"):
            weak.append("SESSION_COOKIE_SECURE is False")
        if not other.get("CSRF_COOKIE_SECURE"):
            weak.append("CSRF_COOKIE_SECURE is False")
        if weak:
            yield CheckResult("security.cookies", "Security", Status.WARNING, "Cookies may be sent over plain HTTP", weak,
                              hint="Run `djdoctor check --deploy` for Django's full deployment checklist.")
