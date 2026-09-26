"""ORM / model / validation errors, system checks, syntax errors and the generic fallback."""

from __future__ import annotations

import re

from django_doctor.branding import CLI_NAME
from django_doctor.diagnostics.check_kb import advice_for
from django_doctor.exit_codes import ExitCode
from django_doctor.explain.models import Category, Confidence, Diagnosis
from django_doctor.explain.rules.base import RuleContext, close_matches, rule
from django_doctor.risk import Risk


@rule("models.field_error")
def field_error(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.find("FieldError")
    if exc is None:
        return None
    msg = exc.message
    m = re.search(r"Cannot resolve keyword '(?P<kw>\w+)' into field\. Choices are: (?P<choices>.*)", msg)
    if m:
        keyword = m.group("kw")
        choices = [c.strip() for c in m.group("choices").split(",") if c.strip()]
        d = ctx.new("models.unknown_lookup_field", Category.MODELS,
                    f"A query uses '{keyword}', which is not a field (or relation) on that model.", exc=exc, risk=Risk.NONE,
                    why="filter()/exclude()/order_by()/values() arguments must be field names, related names or lookups.")
        d.add_evidence(f"Valid names: {', '.join(choices[:25])}" + (" ..." if len(choices) > 25 else ""))
        suggestions = close_matches(keyword, choices)
        for s in suggestions:
            d.add_cause(f"Typo: did you mean '{s}'?", Confidence.LIKELY)
        if not suggestions:
            d.add_cause("The field was renamed/removed, or the lookup should traverse a relation (e.g. author__name).",
                        Confidence.POSSIBLE)
        d.fixes.append("Use one of the valid names above.")
        return d
    m = re.search(r"Unknown field\(s\) \((?P<fields>[^)]*)\) specified for (?P<model>\w+)", msg)
    if m:
        d = ctx.new("models.form_unknown_field", Category.MODELS,
                    f"A ModelForm/admin lists field(s) {m.group('fields')} that {m.group('model')} does not have.",
                    exc=exc, risk=Risk.NONE)
        d.add_cause("A field in Meta.fields (or admin fields/fieldsets) is misspelled or was removed from the model.", Confidence.LIKELY)
        d.add_cause("The field is non-editable (auto_now, editable=False) and cannot be in a form.")
        return d
    m = re.search(r"Related Field got invalid lookup: (?P<lookup>\w+)", msg)
    if m:
        d = ctx.new("models.related_lookup", Category.MODELS,
                    f"The lookup '{m.group('lookup')}' was applied directly to a relation.", exc=exc, risk=Risk.NONE)
        d.add_cause(f"Use a field on the related model, e.g. author__name__{m.group('lookup')} instead of author__{m.group('lookup')}.",
                    Confidence.LIKELY)
        return d
    m = re.search(r"Unsupported lookup '(?P<lookup>\w+)' for (?P<field>\w+)", msg)
    if m:
        d = ctx.new("models.unsupported_lookup", Category.MODELS,
                    f"The lookup '{m.group('lookup')}' is not available for {m.group('field')}.", exc=exc, risk=Risk.NONE)
        d.add_cause("The lookup name is misspelled, or it does not apply to this field type.", Confidence.LIKELY)
        return d
    d = ctx.new("models.field_error", Category.MODELS, "A query or model definition refers to a field incorrectly.",
                exc=exc, risk=Risk.NONE)
    d.add_cause("A field name, lookup or expression is invalid; see the message.", Confidence.LIKELY)
    return d


@rule("models.does_not_exist")
def does_not_exist(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"(?P<model>\w+) matching query does not exist")
    if found:
        exc, match = found
        model = match.group("model")
        d = ctx.new("models.does_not_exist", Category.MODELS,
                    f"`.get()` found no {model} matching the query.", exc=exc, risk=Risk.NONE,
                    why=".get() raises DoesNotExist when no row matches.")
        d.add_cause("The object was deleted, never created, or the lookup value is wrong.", Confidence.LIKELY)
        d.add_cause("The code runs against a different/empty database (e.g. tests without fixtures).")
        d.fixes += ["In views use get_object_or_404(...).",
                    "Elsewhere handle it: `try: ... except Model.DoesNotExist:` or use `.filter(...).first()`."]
        return d
    found = ctx.search(r"get\(\) returned more than one (?P<model>\w+) -- it returned (?P<n>\d+)")
    if found:
        exc, match = found
        d = ctx.new("models.multiple_objects", Category.MODELS,
                    f"`.get()` expected one {match.group('model')} but found {match.group('n')}.", exc=exc, risk=Risk.NONE)
        d.add_cause("The lookup is not unique.", Confidence.DETECTED)
        d.fixes += ["Look up by a unique field (pk, slug with unique=True).",
                    "If duplicates are invalid, add a unique constraint (clean existing duplicates first)."]
        return d
    return None


@rule("models.validation")
def validation_error(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.find("ValidationError")
    if exc is None:
        return None
    d = ctx.new("models.validation", Category.VALIDATION, "A value failed Django field validation.", exc=exc, risk=Risk.NONE,
                why="Model fields convert/validate values in full_clean() and when building queries or saving.")
    msg = exc.message
    patterns = [
        (r"value must be an integer", "An integer field received a non-numeric value."),
        (r"invalid date format|invalid format\. It must be in YYYY-MM-DD", "A date field received a string in the wrong format (expected YYYY-MM-DD)."),
        (r"is not a valid UUID", "A UUIDField received an invalid UUID (often a URL parameter)."),
        (r"value must be either True or False", "A BooleanField received something other than True/False."),
        (r"value must be a decimal number", "A DecimalField received a non-numeric value."),
        (r"Enter a valid email address", "An EmailField received an invalid address."),
    ]
    for pattern, text in patterns:
        if re.search(pattern, msg, re.I):
            d.add_cause(text, Confidence.LIKELY)
            break
    else:
        d.add_cause("The value does not satisfy the field's validators; the message says which rule.", Confidence.LIKELY)
    d.fixes += ["Validate input with a Form/serializer before using it in queries or saving.",
                "Check URL converters (e.g. <int:pk>, <uuid:id>) so invalid values are rejected with 404."]
    return d


@rule("models.value_error_field")
def field_expected(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"Field '(?P<field>\w+)' expected a number but got (?P<value>.+?)\.?$", "ValueError", "TypeError", flags=re.M)
    if not found:
        return None
    exc, match = found
    d = ctx.new("models.value_error_field", Category.MODELS,
                f"The numeric field '{match.group('field')}' received {match.group('value')} in a query.", exc=exc, risk=Risk.NONE)
    d.add_cause("A model instance or string was passed where an id was expected, or a URL parameter was not converted.", Confidence.LIKELY)
    d.fixes.append("Pass the right type (e.g. obj.pk / int(value)) or filter on the relation itself (author=obj).")
    return d


@rule("models.attribute_error")
def attribute_error(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.find("AttributeError")
    if exc is None:
        return None
    msg = exc.first_line
    if "Manager isn't accessible via" in msg:
        d = ctx.new("models.manager_on_instance", Category.MODELS, "`.objects` was used on a model instance instead of the class.",
                    exc=exc, risk=Risk.NONE)
        d.add_cause("Use Model.objects..., not instance.objects...", Confidence.DETECTED)
        return d
    m = re.search(r"'QuerySet' object has no attribute '(?P<attr>\w+)'", msg)
    if m:
        d = ctx.new("models.queryset_attribute", Category.MODELS,
                    f"'{m.group('attr')}' was accessed on a QuerySet (a list of objects), not on a single object.", exc=exc, risk=Risk.NONE)
        d.add_cause("filter()/all() return QuerySets; use get(), first() or iterate.", Confidence.LIKELY)
        return d
    m = re.search(r"object has no attribute 'is_ajax'", msg)
    if m:
        d = ctx.new("models.removed_is_ajax", Category.IMPORTS, "request.is_ajax() was removed in Django 4.0.", exc=exc, risk=Risk.NONE)
        d.fixes.append("Use `request.headers.get('x-requested-with') == 'XMLHttpRequest'` or check the Accept header.")
        d.add_cause("Code written for an older Django version.", Confidence.DETECTED)
        return d
    m = re.search(r"module 'django\.db\.models' has no attribute 'NullBooleanField'", msg)
    if m:
        d = ctx.new("models.removed_nullbooleanfield", Category.IMPORTS, "NullBooleanField was removed in Django 4.0.",
                    exc=exc, risk=Risk.NONE)
        d.fixes.append("Use BooleanField(null=True). Old migrations keep working.")
        return d
    return None


@rule("settings.system_check")
def system_check(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.find("SystemCheckError")
    if exc is None and "System check identified some issues" not in ctx.parsed.raw:
        return None
    exc = exc or ctx.final
    d = ctx.new("settings.system_check", Category.SETTINGS, "Django's system checks found errors, so the command stopped.",
                exc=exc, exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE)
    for m in re.finditer(r"^(?P<obj>\S[^\n]*?): \((?P<id>[\w.]+)\) (?P<msg>.+)$", ctx.parsed.raw, re.M):
        d.add_evidence(f"({m.group('id')}) {m.group('obj')}: {m.group('msg')}")
        advice = advice_for(m.group("id"))
        if advice:
            d.add_cause(f"{m.group('id')}: {advice.explanation}", Confidence.DETECTED)
            d.fixes.append(f"{m.group('id')}: {advice.fix}")
    if not d.causes:
        d.add_cause("See the check messages above; each names the object and problem.", Confidence.DETECTED)
    d.commands.append(f"{CLI_NAME} check")
    return d


@rule("syntax.error")
def syntax_error(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.find("SyntaxError", "IndentationError", "TabError")
    if exc is None:
        return None
    frame = exc.frames[-1] if exc.frames else None
    where = frame.short(ctx.root) if frame else "a Python file"
    d = ctx.new("syntax.error", Category.SYNTAX, f"Python could not parse {where}: {exc.first_line}.",
                exc=exc, exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE)
    if frame and frame.code:
        d.add_evidence(f"Line {frame.line}: {frame.code}")
    d.add_cause("A typo such as an unclosed bracket, a missing colon or mixed tabs/spaces at that line (or just before it).",
                Confidence.DETECTED)
    return d


@rule("async.synchronous_only")
def synchronous_only(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.find("SynchronousOnlyOperation")
    if exc is None:
        return None
    d = ctx.new("async.synchronous_only", Category.MODELS, "Synchronous ORM code ran inside an async context.", exc=exc, risk=Risk.NONE)
    d.add_cause("An async view/consumer called the ORM directly.", Confidence.LIKELY)
    d.fixes += ["Use the async ORM API (aget, acreate, async for) or wrap calls with asgiref.sync.sync_to_async."]
    return d


# ------------------------------------------------------------------ fallback
@rule("generic")
def generic(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.final
    d = ctx.new("generic", Category.UNKNOWN, f"{exc.short_type} was raised: {exc.first_line or '(no message)'}",
                exc=exc, risk=Risk.UNKNOWN)
    d.generic = True
    frame = ctx.parsed.project_frame(ctx.root)
    if frame is not None:
        d.add_evidence(f"The last line of your own code involved: {frame.short(ctx.root)}")
        if frame.code:
            d.add_evidence(f"    {frame.code}")
    if len(ctx.parsed.exceptions) > 1:
        first = ctx.parsed.exceptions[0]
        d.add_evidence(f"Original error: {first.type}: {first.first_line}")
    d.add_cause("Django Doctor has no specific rule for this error; the details above point to where it happened.",
                Confidence.POSSIBLE)
    d.fixes += ["Read the innermost frame from your own code first — that is usually where the fix goes.",
                f"Run `{CLI_NAME} doctor` to rule out configuration, database and migration problems."]
    return d
