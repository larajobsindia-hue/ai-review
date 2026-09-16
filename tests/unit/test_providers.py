import json

import httpx
import pytest

from ai_review.config import AppConfig
from ai_review.models import PromptPayload
from ai_review.providers import LlamaCppProvider, OpenAICompatibleProvider, make_provider
from ai_review.providers.base import LLMProvider, LlamaServerNotFound, ProviderError

PAYLOAD = PromptPayload(system="s", user="u", json_schema={"type": "object"})


def _make_transport(responses=None, handler=None):
    calls = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if handler is not None:
            return handler(request)
        if responses:
            body = responses.pop(0)
            return httpx.Response(200, json=body)
        return httpx.Response(500, json={"error": "boom"})

    return httpx.MockTransport(_handler), calls


def _fake_transport(responses: list[dict]):
    return _make_transport(responses=responses)


def _provider_with(client_cfg, transport, provider_cls=LlamaCppProvider):
    cfg = AppConfig(llm=client_cfg)
    prov = provider_cls(cfg.llm)
    prov.client = httpx.Client(
        transport=transport, base_url=cfg.llm.endpoint, headers=dict(prov.client.headers)
    )
    return prov


def _maybe_raise(exception_cls):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception_cls("boom")
    return handler


def test_make_provider_by_name():
    cfg = AppConfig()
    assert isinstance(make_provider(cfg), LlamaCppProvider)
    cfg.llm.provider = "openai_compatible"
    assert isinstance(make_provider(cfg), OpenAICompatibleProvider)


def test_llamacpp_sends_response_format_when_json_mode():
    transport, calls = _fake_transport([{"choices": [{"message": {"content": "{}"}}]}])
    cfg = AppConfig(llm={"provider": "llamacpp", "json_mode": "on"})
    prov = LlamaCppProvider(cfg.llm)
    prov.client = httpx.Client(transport=transport, base_url=cfg.llm.endpoint)
    prov.send(PAYLOAD)
    body = calls[-1]._content.decode()
    assert '"response_format"' in body
    assert '"json_object"' in body


def test_provider_timeout_sets_timeout():
    cfg = AppConfig(llm={"timeout_seconds": 5})
    p = make_provider(cfg)
    assert p.client.timeout.read == 5


def test_make_provider_returns_abc_subtype():
    assert isinstance(make_provider(AppConfig()), LLMProvider)


def test_make_provider_unknown_name_raises():
    cfg = AppConfig(llm={"provider": "nope"})
    with pytest.raises(ValueError, match="unknown provider"):
        make_provider(cfg)


def test_json_mode_off_omits_response_format():
    transport, calls = _fake_transport([{"choices": [{"message": {"content": "{}"}}]}])
    prov = _provider_with({"provider": "llamacpp", "json_mode": "off"}, transport)
    prov.send(PAYLOAD)
    body = json.loads(calls[-1]._content)
    assert "response_format" not in body


def test_api_key_sets_authorization_header_on_client():
    prov = LlamaCppProvider(AppConfig(llm={"api_key": "sekret"}).llm)
    assert prov.client.headers["Authorization"] == "Bearer sekret"
    assert prov.client.headers["Content-Type"] == "application/json"


def test_api_key_header_sent_on_request():
    transport, calls = _make_transport([{"choices": [{"message": {"content": "{}"}}]}])
    prov = _provider_with({"provider": "llamacpp", "api_key": "secret"}, transport)
    prov.send(PAYLOAD)
    assert calls[-1].headers["authorization"] == "Bearer secret"


def test_custom_headers_merged_into_client():
    prov = LlamaCppProvider(AppConfig(llm={"headers": {"X-Custom": "yes"}}).llm)
    assert prov.client.headers["X-Custom"] == "yes"
    assert prov.client.headers["Content-Type"] == "application/json"


def test_connect_error_raises_llama_server_not_found():
    transport, calls = _make_transport(handler=_maybe_raise(httpx.ConnectError))
    prov = _provider_with({"provider": "llamacpp"}, transport)
    with pytest.raises(LlamaServerNotFound):
        prov.send(PAYLOAD)
    assert calls


def test_timeout_raises_provider_error():
    transport, calls = _make_transport(handler=_maybe_raise(httpx.TimeoutException))
    prov = _provider_with({"provider": "llamacpp", "timeout_seconds": 30}, transport)
    with pytest.raises(ProviderError, match="timed out after 30s"):
        prov.send(PAYLOAD)
    assert calls


def test_other_transport_error_raises_provider_error():
    transport, calls = _make_transport(handler=_maybe_raise(httpx.ReadError))
    prov = _provider_with({"provider": "llamacpp"}, transport)
    with pytest.raises(ProviderError, match="transport error"):
        prov.send(PAYLOAD)
    assert calls


def test_blank_content_raises_provider_error():
    transport, calls = _make_transport([{"choices": [{"message": {"content": "   "}}]}])
    prov = _provider_with({"provider": "llamacpp"}, transport)
    with pytest.raises(ProviderError, match="empty completion"):
        prov.send(PAYLOAD)
    assert calls


def test_invalid_json_body_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    transport, calls = _make_transport(handler=handler)
    prov = _provider_with({"provider": "llamacpp"}, transport)
    with pytest.raises(ProviderError, match="invalid JSON"):
        prov.send(PAYLOAD)
    assert calls


def test_empty_choices_raises_malformed_provider_error():
    transport, calls = _make_transport([{"choices": []}])
    prov = _provider_with({"provider": "llamacpp"}, transport)
    with pytest.raises(ProviderError, match="malformed provider response"):
        prov.send(PAYLOAD)


def test_missing_content_raises_malformed_provider_error():
    transport, calls = _make_transport([{"choices": [{"message": {}}]}])
    prov = _provider_with({"provider": "llamacpp"}, transport)
    with pytest.raises(ProviderError, match="malformed provider response"):
        prov.send(PAYLOAD)


def test_missing_choices_key_raises_malformed_provider_error():
    transport, calls = _make_transport([{"nope": True}])
    prov = _provider_with({"provider": "llamacpp"}, transport)
    with pytest.raises(ProviderError, match="malformed provider response"):
        prov.send(PAYLOAD)


def test_openai_compatible_posts_chat_completions():
    transport, calls = _make_transport([{"choices": [{"message": {"content": "{}"}}]}])
    prov = _provider_with({"provider": "openai_compatible"}, transport, OpenAICompatibleProvider)
    prov.send(PAYLOAD)
    assert calls[-1].url.path == "/chat/completions"


def test_llamacpp_posts_v1_chat_completions():
    transport, calls = _fake_transport([{"choices": [{"message": {"content": "{}"}}]}])
    prov = LlamaCppProvider(AppConfig().llm)
    prov.client = httpx.Client(transport=transport, base_url=prov.cfg.endpoint)
    prov.send(PAYLOAD)
    assert calls[-1].url.path == "/v1/chat/completions"


def test_send_parses_and_metadata():
    transport, calls = _make_transport([{"choices": [{"message": {"content": '{"ok": true}'}}]}])
    cfg = AppConfig(llm={"provider": "openai_compatible", "model": "gpt-4o-mini"})
    prov = OpenAICompatibleProvider(cfg.llm)
    prov.client = httpx.Client(transport=transport, base_url=cfg.llm.endpoint)
    raw = prov.send(PAYLOAD)
    assert raw.text == '{"ok": true}'
    assert raw.provider == "openai_compatible"
    assert raw.endpoint == cfg.llm.endpoint
    assert raw.model == "gpt-4o-mini"
    assert raw.duration_s >= 0.0


def test_system_extra_appended_to_system_content():
    transport, calls = _make_transport([{"choices": [{"message": {"content": "{}"}}]}])
    prov = _provider_with({"provider": "llamacpp", "json_mode": "off"}, transport)
    prov.send(PAYLOAD, system_extra="NOTE: corrective pass")
    body = json.loads(calls[-1]._content)
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == "s\nNOTE: corrective pass"
    assert body["messages"][1] == {"role": "user", "content": "u"}


def test_system_content_untouched_when_system_extra_empty():
    transport, calls = _make_transport([{"choices": [{"message": {"content": "{}"}}]}])
    prov = _provider_with({"provider": "llamacpp", "json_mode": "off"}, transport)
    prov.send(PAYLOAD)
    body = json.loads(calls[-1]._content)
    assert body["messages"][0]["content"] == "s"
    assert body["messages"][1] == {"role": "user", "content": "u"}


def test_model_sent_verbatim_from_config():
    transport, calls = _make_transport([{"choices": [{"message": {"content": "{}"}}]}])
    prov = _provider_with({"provider": "llamacpp", "model": "auto"}, transport)
    prov.send(PAYLOAD)
    body = json.loads(calls[-1]._content)
    assert body["model"] == "auto"


def test_send_is_deterministic_in_body():
    transport_one, calls_one = _make_transport([{"choices": [{"message": {"content": "{}"}}]}])
    transport_two, calls_two = _make_transport([{"choices": [{"message": {"content": "{}"}}]}])
    prov_one = _provider_with({"provider": "llamacpp"}, transport_one)
    prov_two = _provider_with({"provider": "llamacpp"}, transport_two)
    prov_one.send(PAYLOAD)
    prov_two.send(PAYLOAD)
    assert json.loads(calls_one[-1]._content) == json.loads(calls_two[-1]._content)
