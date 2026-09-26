# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security
- `.djdoctor/` state (error logs, SQLite backups) is created owner-only (0700/0600); saved
  error logs are redacted.
- Terminal escape sequences and control characters from logs/tracebacks are stripped before display.
- The inspection probe returns only storage backend names, never their options.
- AI payloads use an allow-list: configuration values, request paths and code lines are never
  sent; `explain --ai-preview` shows the exact payload.
- Release pipeline: PyPI Trusted Publishing with attestations, SHA-pinned actions,
  least-privilege tokens, dependency audit and CodeQL.

### Changed
- The PyPI distribution is named `djdoctor` (`django-doctor` is an unrelated project).

### Added
- SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, issue/PR templates, Dependabot.

## [0.1.0] - 2026-09-26

### Added
- `djdoctor` CLI with `doctor`, `check`, `migrate`, `makemigrations`, `migration-plan`,
  `explain`, `start`, `shell`, `test`, `urls`, `collectstatic`, `info` and `django` pass-through.
- Automatic project detection (manage.py, settings module, interpreter, config file).
- Read-only inspection probe running in the project's own interpreter.
- Safe migration manager: risk classification, rename ambiguity detection, data-at-risk
  row counts, explicit confirmation for destructive operations, SQLite backups, verification.
- Explanation engine: traceback parser and 60+ deterministic rules verified against the project.
- Doctor scan: settings, environment variables, CSRF/CORS, database, migrations, schema
  mismatches, URL names, templates, static files, system checks.
- Optional provider-agnostic AI explanations (Anthropic, OpenAI, Ollama).
- Meaningful, documented exit codes; secret redaction everywhere.
