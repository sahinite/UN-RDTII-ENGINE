"""Ollama offline provider — qwen2.5:7b (P6) and granite3-8b (P7). [Z2-4 ST1]"""

from __future__ import annotations

import os
import time

import requests

from src.mapping.base_provider import BaseLLMProvider
from src.mapping.exceptions import ProviderAPIError, ProviderTimeoutError
from src.mapping.models import LLMResponse

OLLAMA_MODELS = {
    6: "qwen2.5:7b",    # Priority 6 — Apache 2.0
    7: "granite3-8b",   # Priority 7 — Apache 2.0 (IBM Granite 3.0 8B)
}

# CRITICAL: Llama 3.3 is EXPLICITLY EXCLUDED — non-Apache 2.0 license.
LLAMA33_BLOCKLIST = ["llama3.3", "llama-3.3", "llama_3.3", "llama3.3:latest"]

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


class OllamaProvider(BaseLLMProvider):
    def __init__(self, priority: int):
        assert priority in (6, 7), f"Invalid Ollama priority: {priority}"
        self._priority = priority
        self._model = OLLAMA_MODELS[priority]
        assert self._model not in LLAMA33_BLOCKLIST, "Llama 3.3 blocked — non-Apache 2.0 license"

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        base_url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL)
        try:
            r = requests.get(f"{base_url}/api/tags", timeout=3)
            if r.status_code != 200:
                return False
            models = [m["name"] for m in r.json().get("models", [])]
            return any(self._model.replace(":latest", "") in m for m in models)
        except requests.RequestException:
            return False

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> LLMResponse:
        base_url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL)
        payload = {
            "model": self._model,
            "prompt": f"{system_prompt}\n\n{user_prompt}",
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        t0 = time.time()
        try:
            r = requests.post(f"{base_url}/api/generate", json=payload, timeout=120)
            r.raise_for_status()
        except requests.Timeout as e:
            raise ProviderTimeoutError("ollama", str(e))
        except requests.RequestException as e:
            raise ProviderAPIError("ollama", str(e))

        data = r.json()
        latency_ms = (time.time() - t0) * 1000

        return LLMResponse(
            text=data["response"],
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            model=self._model,
            provider="ollama",
            latency_ms=latency_ms,
            cost_usd=0.0,  # self-hosted, no API cost
        )
