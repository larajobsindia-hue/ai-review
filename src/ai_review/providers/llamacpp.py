"""llama.cpp server provider (OpenAI-compatible /v1/chat/completions)."""
from __future__ import annotations

import time

from ai_review.models import PromptPayload, RawLLMResponse

from .base import LLMProvider


class LlamaCppProvider(LLMProvider):
    name = "llamacpp"

    def send(self, payload: PromptPayload, system_extra: str = "") -> RawLLMResponse:
        body = self._build_body(payload, system_extra=system_extra)
        started = time.monotonic()
        response = self._post("/v1/chat/completions", body)
        raw = self._parse_response(response)
        raw.duration_s = time.monotonic() - started
        return raw
