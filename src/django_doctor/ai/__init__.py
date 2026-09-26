"""Optional, provider-agnostic AI explanations.

The deterministic engine always runs first. An AI provider only ever receives
the structured, redacted :class:`~django_doctor.explain.models.Diagnosis` —
never source files, settings values or environment variables — and its answer
is displayed as text. Nothing it returns is ever executed.
"""
