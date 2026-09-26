"""URL routing, templates, requests (CSRF, ALLOWED_HOSTS) and the dev server."""

from __future__ import annotations

import re
from pathlib import Path

from django_doctor.branding import CLI_NAME
from django_doctor.exit_codes import ExitCode
from django_doctor.explain.models import Category, Confidence, Diagnosis
from django_doctor.explain.rules.base import RuleContext, close_matches, quote_list, rule
from django_doctor.project import IGNORED_DIRS
from django_doctor.risk import Risk


# -------------------------------------------------------------------- urls
@rule("urls.no_reverse_match")
def no_reverse_match(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.find("NoReverseMatch")
    if exc is None:
        return None
    msg = exc.message

    ns = re.search(r"'(?P<ns>[\w-]+)' is not a registered namespace(?: inside '(?P<parent>[\w:-]+)')?", msg)
    missing = re.search(r"Reverse for '(?P<name>[^']+)' not found\. '[^']+' is not a valid view function or pattern name", msg)
    args = re.search(
        r"Reverse for '(?P<name>[^']+)' with (?:(?:keyword )?arguments '(?P<args>.*?)'(?: and keyword arguments '(?P<kwargs>.*?)')?|no arguments) "
        r"not found\. (?P<count>\d+) pattern\(s\) tried: \[(?P<patterns>.*)\]",
        msg,
    )

    if ns:
        namespace = ns.group("ns")
        d = ctx.new("urls.unknown_namespace", Category.URLS,
                    f"A URL was reversed with the namespace '{namespace}', but no URLconf registers that namespace.",
                    exc=exc, exit_code=ExitCode.GENERAL_ERROR, risk=Risk.NONE)
        known = (ctx.project.urls or {}).get("namespaces") if ctx.project else None
        if known is not None:
            d.add_evidence("Registered namespaces: " + (quote_list(known) if known else "(none)"), verified=True)
            for m in close_matches(namespace, known):
                d.add_cause(f"Typo: did you mean '{m}'?", Confidence.POSSIBLE)
        d.add_cause(f"The app's urls.py does not set `app_name = '{namespace}'`, or it is included without a namespace.", Confidence.LIKELY)
        d.fixes += [f"Add `app_name = '{namespace}'` to the app's urls.py, or",
                    f"include it with `path('...', include(('app.urls', '{namespace}')))`."]
        d.commands.append(f"{CLI_NAME} urls")
        return d

    if missing:
        name = missing.group("name")
        d = ctx.new("urls.unknown_name", Category.URLS,
                    f"Django couldn't find a URL named '{name}'.", exc=exc, risk=Risk.NONE,
                    why="`reverse()`, `redirect()` and `{% url %}` look URLs up by the `name=` given in urls.py.")
        names = ctx.project.url_names() if ctx.project else None
        if names is not None:
            if name in names:
                d.add_evidence(f"A URL named '{name}' exists now; the running server may be outdated.", verified=True)
            else:
                d.add_evidence(f"No URL pattern is named '{name}'.", verified=True)
                suffix = [n for n in names if n.split(":")[-1] == name.split(":")[-1]]
                if len(suffix) == 1:
                    d.add_cause(f"The URL is namespaced. Use '{suffix[0]}' instead of '{name}'.", Confidence.DETECTED)
                elif suffix:
                    d.add_cause(f"The URL is namespaced. Use one of {quote_list(suffix)} instead of '{name}'.",
                                Confidence.DETECTED)
                for m in close_matches(name, names):
                    if m not in suffix:
                        d.add_cause(f"Typo: did you mean '{m}'?", Confidence.POSSIBLE)
        d.add_cause("The URL name is missing from urls.py or misspelled.", Confidence.LIKELY)
        d.add_cause("The app's urls.py is not included in ROOT_URLCONF.")
        d.fixes += [f"Check urls.py for `name='{name}'` and every `{{% url '{name}' %}}` / `reverse('{name}')`."]
        d.commands.append(f"{CLI_NAME} urls")
        return d

    if args:
        name = args.group("name")
        passed = args.group("args") or args.group("kwargs") or "none"
        d = ctx.new("urls.wrong_arguments", Category.URLS,
                    f"A URL named '{name}' exists, but not one that accepts the arguments {passed}.",
                    exc=exc, risk=Risk.NONE)
        d.add_evidence(f"Patterns tried: {args.group('patterns') or '(none)'}")
        if re.search(r"\(''\s*,?\)|: ''", passed):
            d.add_cause("An argument is an empty string: the template variable used for it is probably "
                        "undefined or empty (e.g. `object.pk` on an unsaved object).", Confidence.LIKELY)
        d.add_cause("The number/names of arguments passed do not match the converters in the URL pattern.", Confidence.LIKELY)
        d.add_cause("A converter such as <int:pk> rejects the value (e.g. a non-numeric value).")
        d.fixes.append("Compare the arguments passed to reverse()/{% url %} with the pattern's converters.")
        d.commands.append(f"{CLI_NAME} urls --filter {name.split(':')[-1]}")
        return d

    d = ctx.new("urls.no_reverse_match", Category.URLS, "Django could not build a URL with reverse()/{% url %}.",
                exc=exc, risk=Risk.NONE)
    d.add_cause("The URL name, namespace or arguments don't match any pattern.", Confidence.LIKELY)
    d.commands.append(f"{CLI_NAME} urls")
    return d


# ---------------------------------------------------------------- templates
@rule("templates.does_not_exist")
def template_does_not_exist(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.find("TemplateDoesNotExist")
    if exc is None:
        return None
    names = [n.strip() for n in exc.first_line.split(",") if n.strip()]
    name = names[0] if names else exc.first_line
    d = ctx.new("templates.does_not_exist", Category.TEMPLATES,
                f"Django could not find the template '{name}' in any template directory.",
                exc=exc, risk=Risk.NONE,
                why="Templates are searched in TEMPLATES['DIRS'] and, when APP_DIRS is True, in each installed app's templates/ folder.")
    conclusive = False
    if ctx.project is not None:
        data = ctx.project.templates([name])
        if data is not None:
            if name in data.get("found", {}):
                d.add_evidence(f"'{name}' is found now at {data['found'][name]}; restart the server if it is still failing.", verified=True)
                conclusive = True
            else:
                dirs = data.get("template_dirs", [])
                d.add_evidence(f"Searched {len(dirs)} template directories; '{name}' is not in any of them.", verified=True)
                for m in close_matches(name, data.get("available", []), cutoff=0.7):
                    d.add_cause(f"Typo or wrong folder: '{m}' exists.", Confidence.POSSIBLE)
        # Look for a file with that name anywhere in the project.
        base = Path(name).name
        for path in _find_files(ctx.project.root, base):
            rel = path.relative_to(ctx.project.root)
            d.add_evidence(f"A file named '{base}' exists at {rel}.", verified=True)
            parts = rel.parts
            if "templates" in parts:
                idx = parts.index("templates")
                expected = "/".join(parts[idx + 1:])
                if expected != name:
                    d.add_cause(f"The template exists but its path relative to the templates folder is '{expected}', not '{name}'.",
                                Confidence.DETECTED)
                    conclusive = True
                elif idx > 0:
                    app_dir = parts[idx - 1]
                    d.add_cause(f"It lives in the '{app_dir}' folder: check that the app is in INSTALLED_APPS and APP_DIRS is True, "
                                "or add the folder to TEMPLATES['DIRS'].", Confidence.LIKELY)
                    conclusive = True
            break
    if not conclusive:
        d.add_cause("The template name is misspelled or the file is in a different folder.", Confidence.LIKELY)
        d.add_cause("The templates directory is not listed in TEMPLATES['DIRS'], or APP_DIRS is False.")
        d.add_cause("The app containing the template is not in INSTALLED_APPS.")
    d.fixes.append("Put the file at <app>/templates/<name> or <project>/templates/<name> and make sure that folder is searched.")
    d.commands.append(f"{CLI_NAME} doctor")
    return d


def _find_files(root: Path, filename: str, limit: int = 3):
    import os

    found = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [x for x in dirnames if x not in IGNORED_DIRS and not x.startswith(".")]
        if filename in filenames:
            yield Path(dirpath) / filename
            found += 1
            if found >= limit:
                return


@rule("templates.syntax")
def template_syntax(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.find("TemplateSyntaxError")
    if exc is None:
        return None
    msg = exc.message
    d = ctx.new("templates.syntax", Category.TEMPLATES, "A template contains invalid template syntax.", exc=exc, risk=Risk.NONE)
    invalid = re.search(r"Invalid block tag on line (?P<line>\d+): '(?P<tag>\w+)'", msg)
    library = re.search(r"'(?P<lib>\w+)' is not a registered tag library", msg)
    filt = re.search(r"Invalid filter: '(?P<filter>\w+)'", msg)
    if invalid:
        tag = invalid.group("tag")
        d.add_evidence(f"Unknown tag '{{% {tag} %}}' on line {invalid.group('line')}.")
        libs = {"static": "static", "get_static_prefix": "static", "trans": "i18n", "translate": "i18n",
                "blocktrans": "i18n", "blocktranslate": "i18n", "get_current_language": "i18n",
                "localize": "l10n", "timezone": "tz", "humanize": "humanize", "crispy": "crispy_forms_tags",
                "render_field": "widget_tweaks", "cache": "cache"}
        if tag in libs:
            d.add_cause(f"The template uses {{% {tag} %}} without `{{% load {libs[tag]} %}}` at the top.", Confidence.LIKELY)
            d.fixes.append(f"Add `{{% load {libs[tag]} %}}` near the top of the template (each template, including children, must load it).")
        elif tag.startswith("end"):
            d.add_cause(f"An `{{% {tag} %}}` has no matching opening tag, or blocks are nested incorrectly.", Confidence.LIKELY)
        else:
            d.add_cause("A custom tag library is not loaded, or the tag name is misspelled.", Confidence.LIKELY)
    elif library:
        lib = library.group("lib")
        d.add_cause(f"`{{% load {lib} %}}` refers to a library that is not registered: the app providing it is "
                    "probably not in INSTALLED_APPS or not installed.", Confidence.LIKELY)
        d.add_cause("For your own tags: the module must live in <app>/templatetags/ (with __init__.py) and the "
                    "server must be restarted after creating it.")
    elif filt:
        d.add_cause(f"The filter '{filt.group('filter')}' does not exist or its library is not loaded.", Confidence.LIKELY)
    else:
        d.add_cause("A tag is not closed, a block is mis-nested, or a tag has the wrong arguments.", Confidence.POSSIBLE)
    d.commands.append(f"{CLI_NAME} doctor")
    return d


# ------------------------------------------------------------ requests
@rule("security.disallowed_host")
def disallowed_host(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"Invalid HTTP_HOST header: '(?P<host>[^']+)'\.(?: You may need to add '(?P<add>[^']+)' to ALLOWED_HOSTS)?")
    if not found:
        return None
    exc, match = found
    host = match.group("add") or match.group("host")
    d = ctx.new("security.disallowed_host", Category.SECURITY,
                f"A request arrived for the host '{match.group('host')}', which is not in ALLOWED_HOSTS.",
                exc=exc, exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE,
                why="ALLOWED_HOSTS protects against HTTP Host header attacks; Django rejects hosts not listed.")
    if ctx.project and (info := ctx.project.info):
        d.add_evidence(f"ALLOWED_HOSTS = {info.get('allowed_hosts')!r} (DEBUG={info.get('debug')})", verified=True)
    d.add_cause(f"'{host}' needs to be added to ALLOWED_HOSTS.", Confidence.LIKELY)
    d.fixes.append(f"Add '{host}' to ALLOWED_HOSTS (avoid '*' in production).")
    return d


_CSRF = [
    (r"Origin checking failed - (?P<origin>\S+) does not match any trusted origins",
     "The request's Origin is not trusted for CSRF.",
     "Add the origin (including scheme) to CSRF_TRUSTED_ORIGINS, e.g. CSRF_TRUSTED_ORIGINS = ['{origin}']."),
    (r"CSRF cookie not set",
     "The CSRF cookie was not sent with the request.",
     "Render the form with {% csrf_token %} from a Django view; for APIs/SPAs call ensure_csrf_cookie or read the csrftoken cookie; "
     "check CSRF_COOKIE_SECURE (HTTPS only) and CSRF_COOKIE_DOMAIN."),
    (r"CSRF token (?:missing|from POST (?:incorrect|has incorrect length))",
     "The POST request did not include a valid CSRF token.",
     "Add {% csrf_token %} inside the <form>, or send the X-CSRFToken header for AJAX requests."),
    (r"Referer checking failed",
     "The HTTPS request's Referer header did not match a trusted origin.",
     "Add the site origin to CSRF_TRUSTED_ORIGINS and check proxy settings (SECURE_PROXY_SSL_HEADER)."),
]


@rule("security.csrf")
def csrf_failure(ctx: RuleContext) -> Diagnosis | None:
    for pattern, what, fix in _CSRF:
        found = ctx.search(pattern)
        if not found:
            continue
        exc, match = found
        d = ctx.new("security.csrf", Category.SECURITY, what, exc=exc,
                    exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE,
                    why="Django's CSRF protection rejected a state-changing request (HTTP 403).")
        origin = match.groupdict().get("origin")
        d.fixes.append(fix.replace("{origin}", origin or ""))
        if ctx.project and (info := ctx.project.info):
            trusted = (info.get("other") or {}).get("CSRF_TRUSTED_ORIGINS")
            d.add_evidence(f"CSRF_TRUSTED_ORIGINS = {trusted!r}", verified=True)
            if origin and trusted is not None and origin not in trusted:
                d.add_cause(f"'{origin}' is not listed in CSRF_TRUSTED_ORIGINS.", Confidence.DETECTED)
        d.add_cause("The frontend and backend run on different origins (ports/domains) or behind a proxy.", Confidence.POSSIBLE)
        return d
    return None


@rule("server.port_in_use")
def port_in_use(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"That port is already in use|Address already in use|\[Errno 98\]|\[Errno 48\]")
    if not found:
        return None
    exc, _ = found
    d = ctx.new("server.port_in_use", Category.SERVER, "The development server could not start because the port is already in use.",
                exc=exc, risk=Risk.NONE)
    d.add_cause("Another runserver (or another program) is already listening on this port.", Confidence.LIKELY)
    d.fixes += ["Stop the other server, or use another port."]
    d.commands += [f"{CLI_NAME} start 8001", "lsof -i :8000   # find the process using the port"]
    return d
