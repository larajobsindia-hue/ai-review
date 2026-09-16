"""Provider invocation with corrective retry on JSON conformance failure."""
from __future__ import annotations

from ai_review.models import PromptPayload, RawLLMResponse, ReviewResult
from ai_review.parser import ParseError, parse_llm_json
from ai_review.providers.base import LLMProvider

CORRECTIVE_HINT = (
    "Your last output was not valid JSON matching the supplied schema. "
    "Return only the JSON object."
)


class ReviewUnavailable(ParseError):
    """Retry with the corrective hint was exhausted without usable JSON.

    Subclasses :class:`ai_review.parser.ParseError` so callers that catch
    ``ParseError`` (the Task 11 interface contract) also catch this, while
    pipeline code can distinguish "transport/parse hiccup" from
    "LLM never conformed even after correction".
    """


def _rebuild(payload: PromptPayload, corrective: bool) -> PromptPayload:
    """Rebuild the payload, appending the corrective hint when requested.

    Matches the documented transport contract: the system message is rebuilt
    as ``system + "\\n\\n" + CORRECTIVE_HINT`` and the ``corrective`` flag is
    set on the rebuilt payload only. ``provider.send(system_extra=...)``
    (Task 10) stays available for other callers.
    """
    system = payload.system + "\n\n" + CORRECTIVE_HINT if corrective else payload.system
    return PromptPayload(
        system=system, user=payload.user, json_schema=payload.json_schema,
        corrective=corrective,
    )


def review(
    provider: LLMProvider,
    payload: PromptPayload,
    *,
    corrective: bool = False,
) -> RawLLMResponse | None:
    """One-shot provider send; appends the corrective hint when *corrective*."""
    return provider.send(_rebuild(payload, corrective))


class ReviewSession:
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.attempts = 0
        self.corrective_used = False

    def run(self, payload: PromptPayload) -> ReviewResult:
        raw = self._send(payload, corrective=False)
        self.attempts = 1
        try:
            return parse_llm_json(raw.text, payload.json_schema)
        except ParseError:
            if payload.corrective:
                raise
        raw = self._send(payload, corrective=True)
        self.attempts = 2
        self.corrective_used = True
        try:
            return parse_llm_json(raw.text, payload.json_schema)
        except ParseError as exc:
            raise ReviewUnavailable(
                "LLM never produced schema-conforming JSON"
            ) from exc

    def _send(self, payload: PromptPayload, corrective: bool) -> RawLLMResponse:
        return self.provider.send(_rebuild(payload, corrective))
