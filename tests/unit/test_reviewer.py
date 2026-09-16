import pytest

from ai_review.config import LLMConfig
from ai_review.models import PromptPayload, RawLLMResponse
from ai_review.parser import ParseError
from ai_review.providers.base import LLMProvider
from ai_review.reviewer import CORRECTIVE_HINT, ReviewSession, ReviewUnavailable, review

GOOD_JSON = '{"decision": "PASS", "summary": "ok", "issues": []}'


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, responses):
        super().__init__(LLMConfig())
        self.responses = list(responses)
        self.calls = 0
        self.payloads = []

    def send(self, payload):
        self.calls += 1
        self.payloads.append(payload)
        text = self.responses.pop(0)
        return RawLLMResponse(text=text, provider=self.name, endpoint="x", duration_s=0.0)


def test_corrective_retry_then_success():
    bad = "not json"
    good = GOOD_JSON
    prov = FakeProvider([bad, good])
    payload = PromptPayload(system="s", user="u", json_schema={})
    session = ReviewSession(prov)
    result = session.run(payload)
    assert result.decision == "PASS"
    assert session.attempts == 2
    assert session.corrective_used is True


def test_two_failures_raise_parse_error():
    prov = FakeProvider(["not json", "still not json"])
    session = ReviewSession(prov)
    with pytest.raises(ParseError):
        session.run(PromptPayload(system="s", user="u", json_schema={}))


def test_success_first_try_counters():
    prov = FakeProvider([GOOD_JSON])
    session = ReviewSession(prov)
    result = session.run(PromptPayload(system="s", user="u", json_schema={}))
    assert result.decision == "PASS"
    assert session.attempts == 1
    assert session.corrective_used is False


def test_review_unavailable_is_parse_error_and_raised_after_exhaustion():
    assert issubclass(ReviewUnavailable, ParseError)
    prov = FakeProvider(["bad 1", "bad 2"])
    session = ReviewSession(prov)
    with pytest.raises(ReviewUnavailable, match="schema-conforming"):
        session.run(PromptPayload(system="s", user="u", json_schema={}))
    assert prov.calls == 2
    assert session.attempts == 2
    assert session.corrective_used is True


def test_review_one_shot_original_system():
    prov = FakeProvider([GOOD_JSON])
    payload = PromptPayload(system="base system", user="u", json_schema={})
    raw = review(prov, payload)
    assert isinstance(raw, RawLLMResponse)
    assert raw.text == GOOD_JSON
    assert prov.calls == 1
    assert prov.payloads[0].system == "base system"
    assert prov.payloads[0].corrective is False


def test_review_one_shot_corrective_appends_hint():
    prov = FakeProvider([GOOD_JSON])
    payload = PromptPayload(system="base system", user="u", json_schema={})
    review(prov, payload, corrective=True)
    assert prov.payloads[0].system == "base system\n\n" + CORRECTIVE_HINT
    assert prov.payloads[0].corrective is True
    assert prov.payloads[0].user == "u"


def test_retry_payload_carries_hint_and_flag():
    prov = FakeProvider(["bad", GOOD_JSON])
    session = ReviewSession(prov)
    session.run(PromptPayload(system="base", user="u", json_schema={}))
    first, second = prov.payloads
    assert first.system == "base" and first.corrective is False
    assert second.system == "base\n\n" + CORRECTIVE_HINT
    assert second.corrective is True


def test_corrective_payload_no_retry_reraises_parse_error():
    prov = FakeProvider(["still bad"])
    session = ReviewSession(prov)
    payload = PromptPayload(system="s", user="u", json_schema={}, corrective=True)
    with pytest.raises(ParseError) as excinfo:
        session.run(payload)
    assert not isinstance(excinfo.value, ReviewUnavailable)
    assert "no JSON object found" in str(excinfo.value)
    assert prov.calls == 1
    assert session.attempts == 1
    assert session.corrective_used is False
