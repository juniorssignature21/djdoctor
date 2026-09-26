"""Exceptions raised by Django Doctor itself (not by the inspected project).

Each carries an exit code and optional human-oriented detail so the CLI can
render them without a traceback.
"""

from __future__ import annotations

from django_doctor.exit_codes import ExitCode


class DoctorError(Exception):
    """Base class for expected, user-facing failures."""

    exit_code: ExitCode = ExitCode.GENERAL_ERROR
    title: str = "Error"

    def __init__(self, message: str, *, detail: str | None = None, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.hint = hint


class ProjectNotFoundError(DoctorError):
    exit_code = ExitCode.PROJECT_NOT_FOUND
    title = "No Django project detected"


class ConfigurationError(DoctorError):
    exit_code = ExitCode.CONFIGURATION_ERROR
    title = "Configuration error"


class InterpreterError(DoctorError):
    exit_code = ExitCode.CONFIGURATION_ERROR
    title = "Python interpreter problem"


class ProbeError(DoctorError):
    """The inspection subprocess could not run or returned garbage."""

    exit_code = ExitCode.GENERAL_ERROR
    title = "Could not inspect the Django project"


class UnsafeOperationError(DoctorError):
    exit_code = ExitCode.UNSAFE_OPERATION
    title = "Confirmation required"
