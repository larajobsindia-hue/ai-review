"""Test/offline static provider used by integration tests and --dry-run demos.

Deterministic and never touches the network: ``send`` pops the next queued
response, falling back to a schema-conforming PASS JSON on exhaustion. The
``system_extra`` parameter mirrors the :class:`LLMProvider` transport contract
but is ignored.
"""
from __future__ import annotations

from ai_review.config import LLMConfig
from ai_review.models import PromptPayload, RawLLMResponse
from ai_review.providers.base import LLMProvider

FALLBACK_RESPONSE = '{"decision":"PASS","summary":"static","issues":[]}'


class StaticProvider(LLMProvider):
    """Scripted provider: queued responses first, then a PASS fallback."""

    name = "static"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        super().__init__(LLMConfig())

    def send(self, payload: PromptPayload, system_extra: str = "") -> RawLLMResponse:
        text = self.responses.pop(0) if self.responses else FALLBACK_RESPONSE
        return RawLLMResponse(
            text=text, provider=self.name, endpoint="static", duration_s=0.0,
        )
