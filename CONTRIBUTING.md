# Contributing to Django Doctor

Thanks for helping! Bug reports, new error explanations and doctor checks are especially welcome.

## Ground rules

Every change must respect the project principle:

> Automate what is deterministic. Explain what is uncertain. Ask before doing anything destructive.

Concretely:

- **Never** add behaviour that deletes data, migrations or files without the explicit
  confirmation flow (`confirm_destructive`). `--yes` must never confirm data loss.
- Inspection (`bridge/probe.py`) must stay **read-only** and must never return secret values.
- Only mark evidence `verified=True` / `Confidence.DETECTED` when it was actually checked.
  Use "likely" / "possible" otherwise.
- Evidence that quotes configuration values must be `private=True` so it is never sent to AI providers.
- Start subprocesses with argument lists — never `shell=True`.

Security issues: follow [SECURITY.md](SECURITY.md), not the public issue tracker.

## Development setup

```bash
git clone https://github.com/juniorssignature21/djpilot && cd djpilot
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
pytest                 # ~1 minute; creates real temporary Django projects
pytest -m "not slow"   # fast unit tests only
tox                    # Django version matrix
```

## Adding things

- **An error explanation:** a function decorated with `@rule("category.name")` in
  `src/django_doctor/explain/rules/`, plus a case in `tests/test_explain_rules.py`. Parse facts
  from the structured error; verify them through `ctx.project` when you can.
- **A doctor check:** a generator yielding `CheckResult`s, registered in `diagnostics/runner.py`,
  with a test in `tests/test_cli_doctor.py`.
- **A command:** a module in `commands/` exposing `register(app)`, listed in `commands/__init__.py`.
- **The probe** (`bridge/probe.py`) runs inside the user's interpreter: standard library + Django
  only, and syntax compatible with every Python version supported by Django 4.2+.

## Pull requests

- One logical change per PR, with tests. CI must pass (lint, tests on the Django matrix, audit).
- Update `CHANGELOG.md` under "Unreleased" for user-visible changes, and the README if behaviour changes.
- By contributing you agree that your contribution is licensed under the MIT License.

## Publishing setup (maintainers, once)

Releases use [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/), so no PyPI
token is ever stored in GitHub:

1. On PyPI, add a *pending trusted publisher* for project `djdoctor`: owner
   `juniorssignature21`, repository `djpilot`, workflow `release.yml`, environment `pypi`.
2. In the GitHub repository settings, create the environment **`pypi`** and add yourself as a
   required reviewer, so every publish needs a manual approval.

## Releasing (maintainers)

1. Bump `__version__` in `src/django_doctor/__init__.py` and move the changelog entries.
2. Create a GitHub release with tag `v<version>`. The `release` workflow builds, checks and
   publishes to PyPI through Trusted Publishing after the `pypi` environment is approved.
