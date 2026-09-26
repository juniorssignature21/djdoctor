# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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
