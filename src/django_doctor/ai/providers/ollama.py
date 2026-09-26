"""Local models via an Ollama server (no data leaves the machine). Stdlib only."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from django_doctor.ai.base import AIProvider, AIUnavailable


class OllamaProvider(AIProvider):
    name = "ollama"
    default_model = None  # e.g. model = "llama3.1"

    def complete(self, system: str, user: str) -> str:
        url = self.options.get("url", "http://localhost:11434").rstrip("/") + "/api/chat"
        body = json.dumps({
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode()
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=float(self.options.get("timeout", 120))) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise AIUnavailable(f"Could not reach Ollama at {url}: {exc}") from exc
        text = ((data.get("message") or {}).get("content") or "").strip()
        if not text:
            raise AIUnavailable("The model returned no text.")
        return text
