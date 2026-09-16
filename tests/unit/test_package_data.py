"""Package data: prompt and config assets must ship inside the ai_review package."""
import importlib.resources


def test_prompt_assets_shipped():
    resource = importlib.resources.files("ai_review").joinpath("prompts", "system.md")
    with resource.open("r", encoding="utf-8") as fh:
        assert fh.read().startswith("You are a senior")


def test_default_config_shipped():
    resource = importlib.resources.files("ai_review").joinpath("config", "default.yaml")
    with resource.open("r", encoding="utf-8") as fh:
        content = fh.read()
    assert "llm:" in content and "llamacpp" in content
