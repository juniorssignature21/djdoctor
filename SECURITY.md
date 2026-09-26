# Security Policy

## Supported versions

Django Doctor is pre-1.0. Security fixes are released for the **latest minor version** only.

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub:
**[Report a vulnerability](https://github.com/juniorssignature21/djpilot/security/advisories/new)**
(repository → *Security* → *Advisories* → *Report a vulnerability*).

Please include what you found, how to reproduce it, the affected version and the impact you
expect. You can expect:

- an acknowledgement within **3 working days**;
- an initial assessment within **10 working days**;
- a fix, a GitHub Security Advisory and a CVE (when applicable) for confirmed issues, with
  credit to you unless you prefer to stay anonymous.

Please give us a reasonable time to release a fix before any public disclosure (we aim for
90 days at most).

## Threat model

Knowing what Django Doctor is designed to protect against helps decide what is a vulnerability.

### Django Doctor runs your project's code

Like `python manage.py`, Django Doctor **imports and executes the inspected project's code**
(settings, installed apps, URLconfs, migrations) using the project's interpreter. The
project's configuration (`[tool.djdoctor]` in `pyproject.toml` or `.djdoctor.toml`, e.g. the
`python` option) is trusted in the same way.

**Only run Django Doctor on projects you would also run `manage.py` on.** Code execution by a
malicious project is expected behaviour, not a vulnerability.

### What Django Doctor guarantees

These are in scope — a violation is a security bug:

1. **No destructive action without explicit confirmation.** Dropping columns/tables,
   unapplying migrations, `flush`/`reset_db`, `collectstatic --clear` require typing a keyword
   on a terminal or `--allow-destructive`. `--yes` never confirms data loss.
2. **Read-only inspection.** Analysis commands (`doctor`, `check`, `migration-plan`,
   `explain`, `info`, `urls`) do not write to the database, create database files or write
   migration files. The only queries beyond metadata are `SELECT COUNT(*)` for data-at-risk evidence.
3. **Secrets are not displayed.** `SECRET_KEY`, database passwords, environment variable values
   and storage/cache credentials are not returned by the inspection process; displayed errors,
   saved error logs and JSON output mask passwords, tokens and credentials in URLs.
4. **Untrusted input is inert.** Log files and piped output given to `djdoctor explain` are parsed
   as data only: they are never executed, and terminal escape sequences in them are stripped.
5. **Local state is private.** `.djdoctor/` (error logs, SQLite backups — which contain your
   data) is created with owner-only permissions (`0700`/`0600`) and git-ignored.
6. **AI is opt-in and inert.** Nothing is sent to an AI provider unless `--ai` is passed on that
   invocation. Only an allow-listed, redacted subset of the diagnosis is sent (never source
   code, setting values, environment values or request paths); `--ai-preview` shows the exact
   payload. AI output is displayed as text and never executed.
7. **No shell interpolation.** All subprocesses are started with argument lists, never through a shell.

### Out of scope

- Code execution by the inspected project itself or its configuration (see above).
- Secrets that you print yourself in project code, or that Django includes in its own output
  which you pipe through `djdoctor explain` (the stream is echoed unchanged, like `tee`).
- Anything under `--debug`, which deliberately prints raw tracebacks.
- Vulnerabilities in Django, database drivers or AI provider SDKs — report those upstream.

## Release integrity

Releases are built and published by GitHub Actions using
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (no long-lived API
tokens) and carry [PEP 740](https://peps.python.org/pep-0740/) build attestations. The package
name on PyPI is **`djdoctor`** — `django-doctor` is an unrelated project.
