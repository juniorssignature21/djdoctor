"""Provider interface and the prompt sent to providers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from django_doctor.security import redact_mapping

SYSTEM_PROMPT = """You help a developer understand an error in their Django project.
You receive a structured report produced by a deterministic diagnostic tool.
Rules:
- Evidence marked verified=true was checked against the project; other evidence comes from the error text only.
- Do not claim certainty the evidence does not support. Use "likely" / "possibly".
- Never recommend destructive actions (dropping tables, deleting migrations, flushing data) without saying they destroy data and suggesting a backup first.
- Any command you mention will be shown to the developer for manual review, never executed automatically.
- Be concise: at most ~200 words, plain text, no headings."""


class AIUnavailable(Exception):
    """The provider is not configured, not installed, or failed."""


class AIProvider(ABC):
    name: str = "base"
    #: Default model; None means the user must configure one.
    default_model: str | None = None

    def __init__(self, model: str | None = None, options: dict[str, Any] | None = None):
        self.model = model or self.default_model
        self.options = options or {}
        if not self.model:
            raise AIUnavailable(f"No model configured for provider '{self.name}'. Set [tool.djdoctor.ai] model = \"...\".")

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the provider's text answer."""


#: Diagnosis fields sent to a provider. Anything else (e.g. request paths) stays local.
AI_FIELDS = ("rule_id", "category", "title", "what_happened", "why", "evidence", "causes", "fixes",
             "commands", "risk", "risk_note", "error_type", "error_message", "location", "generic")


def build_user_prompt(report: dict[str, Any]) -> str:
    """The exact payload sent to a provider: an allow-list of diagnosis fields.

    Excluded: source code lines, evidence quoting configuration values
    (``private`` evidence), request paths. Secrets are masked in what remains.
    """
    safe = {k: report[k] for k in AI_FIELDS if k in report}
    safe["evidence"] = [
        {"text": e["text"], "verified": e["verified"]}
        for e in safe.get("evidence", []) if not e.get("private")
    ]
    safe = redact_mapping(safe)
    # The location includes a line of the user's code; keep only the file reference.
    if safe.get("location"):
        safe["location"] = safe["location"].splitlines()[0]
    return (
        "Diagnostic report (JSON):\n"
        + json.dumps(safe, indent=2, sort_keys=True, default=str)
        + "\n\nExplain the problem and the safest next steps in plain language."
    )
