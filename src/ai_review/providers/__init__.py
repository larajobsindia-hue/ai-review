from ai_review.config import AppConfig
from ai_review.providers.base import LLMProvider
from ai_review.providers.llamacpp import LlamaCppProvider
from ai_review.providers.openai_compatible import OpenAICompatibleProvider

__all__ = ["LLMProvider", "LlamaCppProvider", "OpenAICompatibleProvider", "make_provider"]


def make_provider(cfg: AppConfig) -> LLMProvider:
    name = cfg.llm.provider
    if name == "llamacpp":
        return LlamaCppProvider(cfg.llm)
    if name == "openai_compatible":
        return OpenAICompatibleProvider(cfg.llm)
    raise ValueError(f"unknown provider: {name}")
