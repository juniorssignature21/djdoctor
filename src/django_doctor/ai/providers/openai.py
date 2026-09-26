"""OpenAI provider. Requires ``pip install django-doctor[openai]`` and a configured model."""

from __future__ import annotations

from django_doctor.ai.base import AIProvider, AIUnavailable


class OpenAIProvider(AIProvider):
    name = "openai"
    default_model = None  # must be configured explicitly

    def complete(self, system: str, user: str) -> str:
        try:
            import openai
        except ImportError as exc:
            raise AIUnavailable("The 'openai' package is not installed (pip install 'django-doctor[openai]').") from exc
        try:
            client = openai.OpenAI()
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except Exception as exc:  # the SDK's error hierarchy varies across versions
            raise AIUnavailable(f"OpenAI request failed: {type(exc).__name__}: {exc}") from exc
        text = (response.choices[0].message.content or "").strip() if response.choices else ""
        if not text:
            raise AIUnavailable("The model returned no text.")
        return text
