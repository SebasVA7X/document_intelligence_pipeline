"""
normalizer/client.py — LLM adapter with support for Claude API, Ollama, and no-LLM mode.

Single interface:
    client = LLMClient(backend="claude")   # or "ollama" or "none"
    response_text = client.complete(system, user)

Backends:
  - "claude" : Anthropic API  (requires ANTHROPIC_API_KEY in the environment)
  - "ollama" : Local server   (requires Ollama running on localhost)
  - "none"   : No LLM; returns "" so mapper falls back to additional_content
"""
from __future__ import annotations

import os
from typing import Literal

from normalizer.config import LLM_MODELS, LLM_TEMPERATURE, OLLAMA_URL

Backend = Literal["claude", "ollama", "none"]


class LLMClient:
    def __init__(self, backend: Backend = "claude", model: str | None = None) -> None:
        self.backend = backend
        self.model = model or LLM_MODELS.get(backend, "")
        self._anthropic = None  # lazy init

    # ─── public interface ─────────────────────────────────────────────────────

    def complete(self, system: str, user: str, max_tokens: int = 512) -> str:
        """Send system + user to the LLM and return the response text.

        Returns an empty string if backend is "none" or on any recoverable
        error (mapper treats this as 'no LLM classification').
        """
        if self.backend == "none" or not user.strip():
            return ""
        if self.backend == "claude":
            return self._complete_claude(system, user, max_tokens)
        if self.backend == "ollama":
            return self._complete_ollama(system, user, max_tokens)
        return ""

    # ─── backends ─────────────────────────────────────────────────────────────

    def _complete_claude(self, system: str, user: str, max_tokens: int) -> str:
        try:
            import anthropic
        except ImportError:
            print("  [normalizer] anthropic SDK not installed. Run: pip install anthropic")
            return ""

        if self._anthropic is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                print("  [normalizer] ANTHROPIC_API_KEY not found in environment.")
                return ""
            self._anthropic = anthropic.Anthropic(api_key=api_key)

        try:
            msg = self._anthropic.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=LLM_TEMPERATURE,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return msg.content[0].text if msg.content else ""
        except Exception as e:
            print(f"  [normalizer] Claude API error: {e}")
            return ""

    def _complete_ollama(self, system: str, user: str, max_tokens: int) -> str:
        try:
            import requests
        except ImportError:
            print("  [normalizer] requests not installed. Run: pip install requests")
            return ""

        prompt_len = len(system) + len(user)
        timeout = max(120, prompt_len // 50)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "stream": False,
            "options": {"temperature": LLM_TEMPERATURE, "num_predict": max_tokens},
        }
        try:
            resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "")
        except Exception as e:
            print(f"  [normalizer] Ollama error (timeout={timeout}s): {e}")
            return ""
