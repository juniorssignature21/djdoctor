"""Bridge between the CLI process and the inspected project's interpreter."""

from django_doctor.bridge.runner import ProbeResult, ProbeRunner

__all__ = ["ProbeResult", "ProbeRunner"]
