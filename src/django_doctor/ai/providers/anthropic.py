"""Anthropic (Claude) provider. Requires ``pip install django-doctor[anthropic]``.

Credentials are resolved by the SDK (ANTHROPIC_API_KEY, or an `ant auth login` profile).
"""

from __future__ import annotations

from django_doctor.ai.base import AIProvider, AIUnavailable


class AnthropicProvider(AIProvider):
    name = "anthropic"
    default_model = "claude-opus-5"

    def complete(self, system: str, user: str) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise AIUnavailable("The 'anthropic' package is not installed (pip install 'django-doctor[anthropic]').") from exc
        client = anthropic.Anthropic()
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=int(self.options.get("max_tokens", 4000)),
                system=system,
                messages=[{"role": "user", "content": user}],
                # Server-side fallback on a policy refusal (routes by refusal category).
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise AIUnavailable("Anthropic credentials are missing or invalid (set ANTHROPIC_API_KEY).") from exc
        except anthropic.RateLimitError as exc:
            raise AIUnavailable("Anthropic rate limit reached; try again later.") from exc
        except anthropic.APIStatusError as exc:
            raise AIUnavailable(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIUnavailable("Could not reach the Anthropic API.") from exc
        if response.stop_reason == "refusal":
            raise AIUnavailable("The model declined to answer this request.")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise AIUnavailable("The model returned no text.")
        return text
