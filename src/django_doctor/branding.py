"""Product naming in one place so the tool can be renamed easily.

Everything user-facing (CLI name, headings, config section, state directory)
derives from these constants.
"""

PRODUCT_NAME = "Django Doctor"
CLI_NAME = "djdoctor"
#: Section used in ``pyproject.toml`` (``[tool.djdoctor]``) and the stand-alone
#: config file name (``.djdoctor.toml``).
CONFIG_SECTION = "djdoctor"
CONFIG_FILENAME = ".djdoctor.toml"
#: Per-project directory for local state (last error log, SQLite backups).
STATE_DIRNAME = ".djdoctor"
#: Prefix for environment variables that configure the tool itself.
ENV_PREFIX = "DJDOCTOR_"
