"""Command-line entry point (``djdoctor``)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import typer

from django_doctor import __version__
from django_doctor.branding import CLI_NAME, PRODUCT_NAME
from django_doctor.commands import COMMAND_MODULES
from django_doctor.exit_codes import EXIT_CODE_DOCS
from django_doctor.output.console import Console, Verbosity
from django_doctor.state import AppState

_EXIT_HELP = "\n\n".join(f"{int(code)} = {text}" for code, text in EXIT_CODE_DOCS.items())

app = typer.Typer(
    name=CLI_NAME,
    help=(
        f"{PRODUCT_NAME}: a safe developer-assistance layer around Django's own tooling.\n\n"
        "Automate what is deterministic. Explain what is uncertain. Ask before doing anything destructive."
    ),
    epilog=f"Exit codes:\n\n{_EXIT_HELP}",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"{CLI_NAME} {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    project: Path = typer.Option(None, "--project", "-p", help="Path inside the Django project (default: current directory)."),
    settings: str = typer.Option(None, "--settings", help="Django settings module, e.g. mysite.settings.dev."),
    python: str = typer.Option(None, "--python", help="Python interpreter of the project's virtualenv."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show more detail."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only show warnings, errors and results."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colours."),
    debug: bool = typer.Option(False, "--debug", help="Show raw tracebacks for troubleshooting."),
    timeout: int = typer.Option(120, "--timeout", help="Seconds to wait for project inspection.", hidden=True),
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Show the version and exit."),
) -> None:
    verbosity = Verbosity.QUIET if quiet else (Verbosity.VERBOSE if verbose else Verbosity.NORMAL)
    console = Console(verbosity=verbosity, color=not no_color, debug=debug)
    ctx.obj = AppState(console=console, project_path=project, settings=settings, python=python,
                       debug=debug, timeout=timeout)


for _name in COMMAND_MODULES:
    importlib.import_module(f"django_doctor.commands.{_name}").register(app)


# Flags accepted anywhere on the command line (hoisted before the subcommand).
_GLOBAL_FLAGS = {"--verbose", "-v", "--quiet", "-q", "--no-color", "--debug"}
_GLOBAL_WITH_VALUE = {"--project", "-p", "--settings", "--python"}
#: Commands whose remaining arguments belong to Django, not to us.
_PASSTHROUGH = {"django", "test"}


def normalize_argv(argv: list[str]) -> list[str]:
    """Allow ``djdoctor doctor --verbose`` as well as ``djdoctor --verbose doctor``."""
    command_index = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in _GLOBAL_WITH_VALUE:
            i += 2
            continue
        if not arg.startswith("-"):
            command_index = i
            break
        i += 1
    if command_index is None:
        return argv
    head, command, rest = argv[:command_index], argv[command_index], argv[command_index + 1:]
    hoisted: list[str] = []
    remaining: list[str] = []
    j = 0
    while j < len(rest):
        arg = rest[j]
        if arg == "--" or command in _PASSTHROUGH:
            remaining.extend(rest[j:])
            break
        if arg in _GLOBAL_FLAGS:
            hoisted.append(arg)
        elif arg in _GLOBAL_WITH_VALUE and j + 1 < len(rest):
            hoisted.extend(rest[j:j + 2])
            j += 1
        else:
            remaining.append(arg)
        j += 1
    return head + hoisted + [command] + remaining


def main(argv: list[str] | None = None) -> None:
    args = normalize_argv(list(sys.argv[1:] if argv is None else argv))
    app(args=args, prog_name=CLI_NAME)


if __name__ == "__main__":  # pragma: no cover
    main()
