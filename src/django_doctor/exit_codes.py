"""Process exit codes. These are part of the public interface — keep them stable."""

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    GENERAL_ERROR = 1
    PROJECT_NOT_FOUND = 2
    CONFIGURATION_ERROR = 3
    MIGRATION_PROBLEM = 4
    DATABASE_ERROR = 5
    UNSAFE_OPERATION = 6
    INTERRUPTED = 130


EXIT_CODE_DOCS = {
    ExitCode.SUCCESS: "Success",
    ExitCode.GENERAL_ERROR: "General error (including a failed wrapped Django command)",
    ExitCode.PROJECT_NOT_FOUND: "No Django project detected",
    ExitCode.CONFIGURATION_ERROR: "Configuration / settings / system check error",
    ExitCode.MIGRATION_PROBLEM: "Migration problem (missing, conflicting or failed migrations)",
    ExitCode.DATABASE_ERROR: "Database error (unreachable, bad credentials, schema mismatch)",
    ExitCode.UNSAFE_OPERATION: "Unsafe / destructive operation requires explicit confirmation",
    ExitCode.INTERRUPTED: "Interrupted by the user (Ctrl+C)",
}
