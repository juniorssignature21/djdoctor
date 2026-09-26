"""Provider registry and the single entry point used by the CLI."""

from __future__ import annotations

import os

from django_doctor.ai.base import SYSTEM_PROMPT, AIProvider, AIUnavailable, build_user_prompt
from django_doctor.ai.providers.anthropic import AnthropicProvider
from django_doctor.ai.providers.ollama import OllamaProvider
from django_doctor.ai.providers.openai import OpenAIProvider
from django_doctor.branding import ENV_PREFIX
from django_doctor.config import AIConfig
from django_doctor.explain.models import Diagnosis

PROVIDERS: dict[str, type[AIProvider]] = {
    AnthropicProvider.name: AnthropicProvider,
    OpenAIProvider.name: OpenAIProvider,
    OllamaProvider.name: OllamaProvider,
}


def register_provider(cls: type[AIProvider]) -> None:
    """Register an additional provider (e.g. from a plugin)."""
    PROVIDERS[cls.name] = cls


def explain_with_ai(diagnosis: Diagnosis, *, provider: str | None = None,
                    config: AIConfig | None = None) -> tuple[str, str]:
    config = config or AIConfig()
    name = provider or config.provider or os.environ.get(f"{ENV_PREFIX}AI_PROVIDER")
    if not name:
        raise AIUnavailable(
            "no provider configured. Use --ai-provider, set [tool.djdoctor.ai] provider, "
            f"or {ENV_PREFIX}AI_PROVIDER. Available: {', '.join(sorted(PROVIDERS))}."
        )
    cls = PROVIDERS.get(name)
    if cls is None:
        raise AIUnavailable(f"unknown provider '{name}'. Available: {', '.join(sorted(PROVIDERS))}.")
    model = config.model if (config.provider in (None, name)) else None
    instance = cls(model=model or os.environ.get(f"{ENV_PREFIX}AI_MODEL"), options=config.options)
    text = instance.complete(SYSTEM_PROMPT, build_user_prompt(diagnosis.to_dict()))
    return text, f"{name}:{instance.model}"
