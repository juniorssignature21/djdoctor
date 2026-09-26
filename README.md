# Django Doctor

**A safe developer-assistance CLI for Django projects.** Django Doctor detects your
project, runs Django's own tooling for you, manages migrations *safely*, and turns
confusing tracebacks into explanations you can act on.

> Automate what is deterministic. Explain what is uncertain. Ask before doing anything destructive.

```console
$ djdoctor migrate
Django Doctor — Migration Manager

✓ Django project detected
✓ Django 5.2.17 detected
✓ Settings loaded (mysite.settings)
✓ Database connection successful
✓ System checks passed

Model changes detected:
  students.Student
    - Remove field phone from student  (HIGH)
        Removing Student.phone permanently deletes the column and the data stored in it.
        Data at risk: 3 row(s) currently have a value in this column
    + Add field phone_number to student

⚠ Potentially destructive migration
...
  Ambiguous: Student.phone removed and Student.phone_number added.
  Django Doctor does not assume they hold the same data.

Recommended strategy:
  Student.phone removed and Student.phone_number added — possibly a rename
    1. If this IS a rename (same data): use a RenameField migration so data is kept ...
    2. If it is NOT a rename, migrate safely in steps:
      1. Add phone_number (keep phone for now)
      2. Copy existing phone values into phone_number with a data migration (RunPython)
      3. Verify the data
      4. Remove phone in a later migration

✗ Django Doctor will not create and apply this migration automatically.
```

---

## Contents

- [Why it exists](#why-it-exists)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Migration safety](#migration-safety)
- [Error diagnostics](#error-diagnostics)
- [The doctor scan](#the-doctor-scan)
- [Configuration](#configuration)
- [AI integration (optional)](#ai-integration-optional)
- [Exit codes](#exit-codes)
- [Security](#security)
- [Compatibility](#compatibility)
- [How it works](#how-it-works)
- [Development](#development) · [Testing](#testing) · [Contributing](#contributing)
- [Roadmap](#roadmap) · [License](#license)

## Why it exists

Django's management commands are excellent but low-level. Day to day, developers:

- run `makemigrations` and `migrate` without noticing that a "rename" is really a
  *remove + add* that will delete a column full of data;
- stare at `ProgrammingError: column x.y does not exist` without knowing whether the
  migration is missing, unapplied or was faked;
- lose time on `NoReverseMatch`, `TemplateDoesNotExist`, missing environment variables
  and virtualenv mix-ups.

Django Doctor is **not a replacement for Django**. It is a layer around Django's own
APIs (`MigrationLoader`, `MigrationExecutor`, `MigrationAutodetector`, the system check
framework, the URL resolver and template loaders) that adds safety checks and
explanations — and never silently does anything destructive.

## Installation

```bash
pip install django-doctor
```

Install it into the **same virtualenv as your project** (recommended), or point it at the
project's interpreter with `--python` / `DJDOCTOR_PYTHON`. Django itself is not a hard
dependency of the tool: Django Doctor always uses the Django installed for your project.

Optional extras for AI explanations: `pip install "django-doctor[anthropic]"` or
`"django-doctor[openai]"`.

To try it from a checkout:

```bash
git clone https://github.com/juniorssignature21/djpilot && cd djpilot
pip install -e ".[dev]"
```

## Quick start

```bash
cd myproject          # any directory inside the project works
djdoctor doctor       # full diagnostic scan
djdoctor migrate      # create + apply migrations, safely
djdoctor start        # runserver with startup diagnostics
```

When something breaks:

```bash
djdoctor explain                                    # explain the last captured error
djdoctor explain error.log                          # explain the latest error in a log file
python manage.py runserver 2>&1 | djdoctor explain  # live: explain errors as they happen
```

## Commands

| Command | What it does |
|---|---|
| `djdoctor doctor` | Full scan: project, dependencies, settings, environment variables, CSRF/CORS, database, migrations, URLs, templates, static files, system checks. `--deploy`, `--strict`, `--json`, `--only`, `--skip`. |
| `djdoctor check` | Django system checks, with plain-language explanations for common check ids. `--deploy`, `--tag`, `--fail-level`. |
| `djdoctor migrate [APP] [MIGRATION]` | Detect model changes, create migrations, review risks, apply, verify. `--dry-run`, `--yes`, `--allow-destructive`, `--no-makemigrations`, `--no-backup`, `--database`. |
| `djdoctor makemigrations [APPS...]` | Create migrations after a safety review. `--dry-run`, `--check` (CI), `--interactive` (Django's rename/default prompts), `--name`. |
| `djdoctor migration-plan [APP] [MIGRATION]` | Read-only plan: model changes, pending migrations, risk, data at risk, recommended strategy. `--json`, `--check`. |
| `djdoctor explain [FILE]` | Explain an error from a file, a pipe, `--text`, or the last captured error. `--json`, `--no-inspect`, `--ai`. |
| `djdoctor start [ADDRPORT]` | `runserver` after checking settings, database, checks and migrations; captures errors for `explain`. |
| `djdoctor shell` | `manage.py shell` after verifying the project loads. Extra args pass through. |
| `djdoctor test` | Runs `manage.py test` (or pytest when configured); explains environment failures. |
| `djdoctor urls` | Table of URL patterns, names and views. `--filter`, `--json`. |
| `djdoctor collectstatic` | Checks STATIC_ROOT, confirms, runs collectstatic. `--clear` requires destructive confirmation. |
| `djdoctor info` | What was detected: paths, versions, apps, databases — secrets masked. |
| `djdoctor django <command> [args]` | Pass-through to any management command (`djdoctor django showmigrations`). Errors are explained; `flush`/`reset_db` need confirmation. |

Global options work before or after the command: `--project PATH`, `--settings MODULE`,
`--python PATH`, `--verbose/-v`, `--quiet/-q`, `--no-color`, `--debug` (show raw tracebacks).

## Migration safety

`djdoctor migrate` performs these steps:

1. Detect the project and load Django (settings errors are explained, not dumped).
2. Test the database connection (a missing SQLite file is reported, never created by inspection).
3. Run Django's system checks.
4. Ask Django's autodetector which model changes have no migration yet, and what questions
   `makemigrations` would ask (renames, defaults for NOT NULL fields).
5. Classify every operation — both new changes and **existing unapplied migration files**
   (for example ones pulled from a teammate).
6. Stop and ask before anything that can lose data.
7. Create the migration with Django's own `makemigrations`.
8. Back up SQLite databases before destructive operations (`.djdoctor/backups/`).
9. Apply with Django's own `migrate`.
10. Re-read the migration state and the real schema to verify.

### Risk levels

| Risk | Examples |
|---|---|
| **NONE** | CreateModel, nullable AddField, Meta options, `RunPython.noop` |
| **LOW** | RenameField/RenameModel (data kept), AddIndex, one-off defaults |
| **MEDIUM** | NOT NULL field without default, type changes, new unique constraints, custom RunPython/RunSQL |
| **HIGH** (destructive) | RemoveField, DeleteModel, shrinking `max_length`, DROP/DELETE/TRUNCATE in RunSQL, unapplying migrations (`migrate app zero`) |

HIGH-risk operations are never applied automatically:

- On an interactive terminal you must type `apply`.
- In scripts/CI you must pass `--allow-destructive` after reviewing.
- `--yes` answers ordinary prompts only — **it never confirms data loss**.
- Without confirmation the command exits with code **6** and changes nothing.

Evidence is concrete: Django Doctor counts (read-only `SELECT COUNT(*)`) how many rows
would lose data. Operations on tables created earlier in the same plan, or on a database
that does not exist yet, are recognised as harmless.

### Ambiguous changes are not guessed

If `phone` disappears and `phone_number` appears, Django Doctor does not assume it is a
rename. It shows both interpretations: a `RenameField` (keeps data) or a safe multi-step
data migration (add → copy → verify → remove). Candidates are marked as coming from
Django's own rename detection or from Django Doctor's heuristic.

### What Django Doctor never does

It never deletes migration files, drops tables or columns on its own, resets migrations,
flushes or recreates databases, or edits the `django_migrations` table.

### Migration plan

```console
$ djdoctor migration-plan
Migration Plan

Model changes without a migration (makemigrations would create)
  App: students
    students/migrations/0002_remove_student_phone_student_phone_number.py
      - Remove field phone from student  (HIGH)
          Removing Student.phone permanently deletes the column and the data stored in it.
          Data at risk: 3 row(s) currently have a value in this column
      + Add field phone_number to student

Ambiguous changes
  ⚠ Student.phone → phone_number  (Django asked about a rename)
  Django Doctor does not assume these are the same data.

Risk: HIGH — Removing Student.phone permanently deletes the column and the data stored in it.

Recommended strategy
  ...
No changes have been applied.
```

Use `--json` for tooling and `--check` in CI (exit code 4 unless everything is up to date).

## Error diagnostics

`djdoctor explain` parses Python/Django output (chained tracebacks, PostgreSQL
`DETAIL`/`HINT` lines, pytest `E` prefixes, docker-compose prefixes, ANSI colours) into a
structured error, runs deterministic rules, and — when run inside a project — **verifies
hypotheses against the project** by inspecting the migration graph, URL resolver,
template loaders and settings.

```console
$ djdoctor explain

DATABASE ERROR
django.db.utils.OperationalError

What happened
  The Django model expects this database column:

      students_student.email

  but the database does not contain it.

Evidence
  ✓ Model students.Student defines field 'email' (column 'email').
  ✓ Migration students.0003_student_email (Add field email to student) exists but is NOT applied.
  ✓ Migration status: Pending

Detected:
  The migration for this change exists but has not been applied to the database.

Recommended fix
  1. Apply the pending migration(s).

Commands to try
    djdoctor migrate

Risk level: LOW — Adding a missing column with a migration does not remove existing data.
```

Every explanation contains: what happened, why, evidence, causes, recommended fix,
commands to try and a risk level. Language is calibrated:

- `✓` evidence was **verified** against your project; `•` evidence comes from the error text only.
- **Detected** — verified fact. **Likely cause** — typical for this error. **Possible cause** — plausible, unverified.

Covered errors include `NoReverseMatch` (unknown name, namespace, wrong arguments —
with "did you mean"), `TemplateDoesNotExist` (searches the project for the file),
`TemplateSyntaxError` (missing `{% load %}`), `ImproperlyConfigured` (empty SECRET_KEY,
missing DB driver, bad ENGINE, `DATABASE_URL` not set, URLconf without patterns, …),
`ModuleNotFoundError` (maps import names to pip packages, checks your requirements),
`ImportError` (circular imports, APIs removed in Django 3.0–5.1), `OperationalError` /
`ProgrammingError` (missing tables/columns across SQLite, PostgreSQL and MySQL;
connection, credentials, missing database, locked SQLite), `IntegrityError`, `DataError`,
`FieldError` (with close-match suggestions), `ValidationError`, `DoesNotExist`, migration
conflicts, inconsistent history, missing migration parents, missing environment variables
(`os.environ`, django-environ, python-decouple), `DisallowedHost`, CSRF failures,
`AppRegistryNotReady`, system check errors, syntax errors and more. Anything unknown gets
an honest generic explanation pointing at the innermost frame of *your* code.

`start`, `test`, `migrate` and `django` save the last error to `.djdoctor/last_error.log`
(git-ignored automatically), which plain `djdoctor explain` picks up.

## The doctor scan

```console
$ djdoctor doctor
Django Doctor

✓ Project
✓ Dependencies
✓ Settings
⚠ Environment
  ⚠ 1 environment variable(s) are not set and have no default (they will be None)
      PAYMENTS_API_URL (mysite/settings.py:43, used for PAYMENTS_API_URL)
✓ Security
✓ Database
⚠ Migrations
  ⚠ 1 model change(s) have not been migrated
      students: Add field email to student
      djdoctor migration-plan
      djdoctor migrate
✗ URLs
  ✗ 1 URL name(s) used in code/templates do not exist (NoReverseMatch at runtime)
      'students:indx' used at students/views.py:9 — did you mean 'students:index'?
      djdoctor urls
✓ Templates
✓ Static files
✓ System checks

Summary:
  1 error
  2 warnings
  13 checks passed
```

(`--verbose` also lists every passed check.) When settings cannot be imported at all —
for example a required environment variable is missing — the dependent categories are
skipped and the settings error is explained in full.

Checks performed:

- **Project** — manage.py, settings module and where it came from, interpreter, inactive virtualenvs.
- **Dependencies** — Django/Python versions, Django end-of-support dates, packages listed in
  `requirements*.txt` / `pyproject.toml` / `Pipfile` that are not installed.
- **Settings** — settings import and app loading (explained on failure), SECRET_KEY strength
  (never shown), `ALLOWED_HOSTS`, duplicate apps.
- **Environment** — static (AST) scan of settings for `os.environ[...]`, `os.getenv`,
  django-environ, decouple and dj-database-url; missing variables, `.env` files that nothing loads.
- **Security** — `CSRF_TRUSTED_ORIGINS` format, CSRF middleware, django-cors-headers setup and
  order, allow-all origins with credentials, insecure cookies with `DEBUG=False`.
- **Database** — connectivity for every alias (read-only, short timeouts).
- **Migrations** — conflicts, broken graph, inconsistent history, unapplied migrations, model
  changes without migrations, apps with models but no migrations, applied migrations whose files
  are missing, and **model/schema mismatches** (tables/columns missing although migrations are applied).
- **URLs** — URLconf loads; every literal URL name used in `reverse()`, `redirect()` and
  `{% url %}` exists.
- **Templates** — every literal template name used in code/templates exists; project templates compile.
- **Static files** — `STATIC_URL`/`STATIC_ROOT`/`STATICFILES_DIRS`/`MEDIA_*` sanity, and every
  `{% static %}` path is found.
- **System checks** — Django's check framework (`--deploy` adds deployment checks).

## Configuration

Optional, in `pyproject.toml` (or a standalone `.djdoctor.toml` without the `tool.` prefix):

```toml
[tool.djdoctor]
settings = "mysite.settings.dev"   # default: from manage.py / DJANGO_SETTINGS_MODULE
python = ".venv/bin/python"        # interpreter of the project's virtualenv
manage_py = "manage.py"
env_file = ".env"                  # loaded into the environment of inspected commands
skip_checks = ["Security", "environment.missing_optional"]   # categories or check ids

[tool.djdoctor.ai]
provider = "anthropic"             # anthropic | openai | ollama
model = "claude-opus-5"
```

Precedence: command-line option > environment (`DJANGO_SETTINGS_MODULE`, `DJDOCTOR_PYTHON`)
> config file > `manage.py` > heuristics.

## AI integration (optional)

The deterministic engine always works without AI. With `--ai`, the structured explanation
can be sent to a provider for an additional plain-language summary:

```bash
export ANTHROPIC_API_KEY=...
djdoctor explain --ai --ai-provider anthropic
```

```
Django Error → deterministic parsing & rules → verified Diagnosis → (optional) AI summary
```

Guarantees:

- Only the **redacted structured report** is sent — never source files, settings values or
  environment variables. The line of your code in the location is stripped.
- Opt-in on every invocation (`--ai`); nothing is ever sent automatically.
- Output is displayed as text marked *unverified*. **Nothing an AI returns is executed.**
- Provider-agnostic: `anthropic` (default model `claude-opus-5`), `openai` (model must be
  configured), `ollama` (local models; nothing leaves your machine). Add your own by
  subclassing `django_doctor.ai.base.AIProvider` and calling
  `django_doctor.ai.registry.register_provider`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | General error (including a failed wrapped Django command; `doctor --strict` with warnings) |
| 2 | No Django project detected |
| 3 | Configuration / settings / system check error |
| 4 | Migration problem (missing, conflicting or failed migrations) |
| 5 | Database error (unreachable, bad credentials, schema mismatch) |
| 6 | Unsafe / destructive operation requires explicit confirmation |
| 130 | Interrupted (Ctrl+C) |

`djdoctor test` and `djdoctor django …` return the wrapped command's own exit code.

## Security

- Secrets are never printed: the inspection process never returns `SECRET_KEY`, database
  passwords or environment variable values; displayed text passes through a redactor
  (`password=…`, `scheme://user:pass@`, bearer tokens, PostgreSQL `DETAIL` key values).
- Inspection is **read-only**: no writes to the database, no creation of SQLite files, no
  migration files written during analysis. The only reads beyond metadata are `COUNT(*)`
  queries for data-at-risk evidence.
- Destructive operations always need explicit confirmation (see above).
- No source code is ever sent anywhere; the AI layer is opt-in and cannot execute anything.

## Compatibility

- Python 3.10+ for the CLI. The inspected project may use any interpreter Django supports.
- Django 4.2 LTS, 5.0, 5.1, 5.2 LTS, 6.x. Tested in this repository against 4.2, 5.2 and 6.1.
- Database-agnostic: SQLite, PostgreSQL, MySQL/MariaDB, Oracle — anything Django supports.
  Error rules understand SQLite, PostgreSQL and MySQL messages. Automatic backups are SQLite-only.
- No assumptions about DRF, Celery, Redis or project layout (nested apps, settings packages,
  `src/` layouts via `--project`).

## How it works

```
djdoctor (your terminal)                    project interpreter (subprocess)
┌────────────────────────────┐   JSON     ┌──────────────────────────────────┐
│ CLI (Typer) + commands     │ ◀────────▶ │ bridge/probe.py  (read-only)     │
│ diagnostics / explain /    │            │ django.setup(), checks, loader,  │
│ migration planner & safety │            │ executor, autodetector, resolver │
└──────────┬─────────────────┘            └──────────────────────────────────┘
           │ runs Django's own commands for changes
           ▼
   manage.py makemigrations / migrate / runserver / …
```

Inspection runs in a **separate process** using the project's interpreter. This isolates
crashes in project code, lets Django Doctor explain settings errors instead of crashing,
supports a different virtualenv via `--python`, and keeps every command independent.

```
src/django_doctor/
├── cli.py, state.py, config.py, project.py, security.py, branding.py, exit_codes.py
├── bridge/        probe.py (runs inside the project), runner.py, manage.py
├── diagnostics/   doctor checks (settings, database, migrations, urls, files, system, envvars)
├── explain/       traceback parser, rules/, engine, project context
├── migration/     planner.py, safety.py, executor.py, backup.py
├── commands/      one module per CLI command
├── output/        console and formatters
└── ai/            provider-agnostic optional layer
```

Renaming the tool: product name, CLI name, config section and state directory live in
`src/django_doctor/branding.py` (plus the script entry in `pyproject.toml`).

## Development

```bash
pip install -e ".[dev]"
ruff check src tests
pytest
```

## Testing

The suite creates **real temporary Django projects** (and a pre-migrated template project
copied per test) and covers: project detection, settings discovery, command execution,
no changes / new model / new field / removed field / renamed field / NOT NULL field,
pending migrations, migration conflicts, faked migrations (schema mismatch), backwards
plans, unavailable database, invalid settings, missing environment variables, traceback
parsing, 60+ explanation rules, doctor checks, CLI behaviour, secret redaction and the AI layer.

```bash
pytest                  # everything (~1 minute)
pytest -m "not slow"    # fast unit tests only
tox                     # Django 4.2 / 5.2 / 6.x matrix
```

## Contributing

Contributions are welcome — especially new explanation rules.

- **A new error rule**: add a function decorated with `@rule("category.name")` in
  `src/django_doctor/explain/rules/` and a case to `tests/test_explain_rules.py`. Extract
  facts from the structured error, verify them through `ctx.project` when possible, and only
  use `Confidence.DETECTED` for verified facts.
- **A new doctor check**: a generator yielding `CheckResult`s, registered in
  `diagnostics/runner.py`.
- **A new command**: a module in `commands/` with `register(app)`, listed in
  `commands/__init__.py`.
- Keep the probe (`bridge/probe.py`) standard-library + Django only and read-only.

## Roadmap

- Data-migration generator for confirmed rename/copy strategies (written as a reviewable file).
- `djdoctor migrate --squash` guidance and squash-safety analysis.
- Deployment profiles (`doctor --profile production`) and more deployment checks.
- More database-specific diagnostics (PostgreSQL locks, long-running migrations).
- Editor integrations (VS Code problem matcher) and a JSON-lines streaming mode.

## License

MIT — see [LICENSE](LICENSE).
