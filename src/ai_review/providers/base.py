"""Common provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from ai_review.config import LLMConfig
from ai_review.models import PromptPayload, RawLLMResponse


class ProviderError(RuntimeError):
    pass


class LlamaServerNotFound(ProviderError):
    pass


class LLMProvider(ABC):
    name = "base"

    def __init__(self, llm_cfg: LLMConfig) -> None:
        self.cfg = llm_cfg
        self.client = httpx.Client(
            base_url=llm_cfg.endpoint,
            timeout=llm_cfg.timeout_seconds,
            headers={"Content-Type": "application/json", **llm_cfg.headers},
        )
        if llm_cfg.api_key:
            self.client.headers["Authorization"] = f"Bearer {llm_cfg.api_key}"

    def _json_params(self) -> dict:
        return (
            {"response_format": {"type": "json_object"}}
            if self.cfg.json_mode != "off"
            else {}
        )

    def _build_body(self, payload: PromptPayload, system_extra: str = "") -> dict:
        system = payload.system
        if system_extra:
            system = f"{system}\n{system_extra}"
        return {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": payload.user},
            ],
            "max_tokens": self.cfg.max_tokens,
            "temperature": 0.1,
            "stream": False,
            **self._json_params(),
        }

    @abstractmethod
    def send(self, payload: PromptPayload, system_extra: str = "") -> RawLLMResponse:
        """Send the payload; when *system_extra* is non-empty it is appended to
        the system message content as ``system + "\\n" + system_extra``."""
        raise NotImplementedError

    def _post(self, path: str, body: dict) -> httpx.Response:
        try:
            return self.client.post(path, json=body)
        except httpx.ConnectError as exc:
            raise LlamaServerNotFound(
                f"cannot connect to {self.cfg.endpoint}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"LLM request timed out after {self.cfg.timeout_seconds}s"
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderError(f"transport error talking to {self.cfg.endpoint}: {exc}") from exc

    def _parse_response(self, response: httpx.Response) -> RawLLMResponse:
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"invalid JSON from provider: {response.status_code}"
            ) from exc
        try:
            choices = data["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"malformed provider response: {str(data)[:500]!r}") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("empty completion from provider")
        return RawLLMResponse(
            text=content,
            provider=self.name,
            endpoint=self.cfg.endpoint,
            duration_s=0.0,
            model=self.cfg.model,
        )
