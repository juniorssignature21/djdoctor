"""CLI commands. Each module exposes ``register(app)``.

To add a command: create a module here with a ``register(app: typer.Typer)``
function and add it to :data:`COMMAND_MODULES`.
"""

COMMAND_MODULES = [
    "doctor", "check", "migrate", "makemigrations", "migration_plan", "explain", "start",
    "shell", "test", "urls", "collectstatic", "info", "passthrough",
]
