"""Registry: explicit registration, opt-in enablement, applicability skips."""
import pytest

from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import ProfileEntry, RepoProfile, StagedChange
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import build_context


class _Fake(StaticAnalyzer):
    name = "fake"
    executable = "fake"
    languages = ("Python",)
    extensions = (".py",)

    def build_argv(self, exe, ctx):  # pragma: no cover - not exercised here
        return []

    def parse(self, run, ctx):  # pragma: no cover
        return []


def _ctx(tmp_path, *tools, languages=("Python",)):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = AppConfig()
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig() for name in tools}
    profile = RepoProfile(languages=[ProfileEntry(lang, 0.9, []) for lang in languages])
    return cfg.static_analysis, build_context(
        str(tmp_path), cfg, [StagedChange(path="a.py", status="modified")], profile)


def test_register_rejects_duplicates_unless_replacing():
    try:
        registry.register(_Fake, replace=True)
        assert registry.REGISTRY["fake"] is _Fake
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_Fake)
    finally:
        registry.REGISTRY.pop("fake", None)


def test_register_requires_a_name():
    class Nameless(StaticAnalyzer):
        def build_argv(self, exe, ctx):  # pragma: no cover
            return []

        def parse(self, run, ctx):  # pragma: no cover
            return []

    with pytest.raises(ValueError, match="name"):
        registry.register(Nameless)


def test_build_candidates_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "fake", _Fake)
    cfg, ctx = _ctx(tmp_path)                      # configured: nothing
    analyzers, tools = registry.build_candidates(cfg, ctx)
    assert analyzers == [] and tools == []


def test_disabled_tool_is_absent_not_skipped(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "fake", _Fake)
    cfg, ctx = _ctx(tmp_path)
    cfg.tools = {"fake": StaticAnalysisToolConfig(enabled=False)}
    analyzers, tools = registry.build_candidates(cfg, ctx)
    assert analyzers == [] and tools == []


def test_build_candidates_skips_technology_mismatch(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "fake", _Fake)
    cfg, ctx = _ctx(tmp_path, "fake", languages=("Go",))
    analyzers, tools = registry.build_candidates(cfg, ctx)
    assert analyzers == []
    assert [t.name for t in tools] == ["fake"]
    assert tools[0].status == "skipped" and "applicable" in tools[0].error


def test_build_candidates_returns_enabled_applicable_analyzer(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "fake", _Fake)
    cfg, ctx = _ctx(tmp_path, "fake")
    analyzers, tools = registry.build_candidates(cfg, ctx)
    assert [a.name for a in analyzers] == ["fake"] and tools == []


def test_analyzer_loader_is_idempotent_and_registers_every_declared_module():
    from importlib import import_module

    from ai_review.static_analysis import analyzers

    analyzers.load_all()
    analyzers.load_all()                           # idempotent
    for name in analyzers.MODULES:
        module = import_module(f"{analyzers.__name__}.{name}")
        assert any(cls.__module__ == module.__name__
                   for cls in registry.REGISTRY.values()), name
