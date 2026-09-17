"""Doctor probes for the static-analysis layer: warn, never error."""
import pytest

from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import RepoProfile
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.doctor import probe_static_analysis


class StubAnalyzer(StaticAnalyzer):
    name = "stub"
    executable = "stub"

    def build_argv(self, exe, ctx):  # pragma: no cover - never executed here
        return [exe]

    def parse(self, run, ctx):  # pragma: no cover - never executed here
        return []


def _cfg(**tools):
    cfg = AppConfig()
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig(enabled=enabled)
                                 for name, enabled in tools.items()}
    return cfg


def test_disabled_layer_is_a_single_warning():
    cfg = _cfg()
    cfg.static_analysis.enabled = False
    probes = probe_static_analysis(cfg, "/repo", RepoProfile())
    assert [p.status for p in probes] == ["warn"]
    assert "disabled" in probes[0].note


def test_missing_binary_is_a_warning_not_an_error(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: None)
    probes = probe_static_analysis(_cfg(stub=True), "/repo", RepoProfile())
    stub = next(p for p in probes if "stub" in p.label)
    assert stub.status == "warn" and stub.note == "not installed"


def test_installed_tool_reports_its_version(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr(StaticAnalyzer, "version", lambda self, ctx: "stub 1.2.3")
    probes = probe_static_analysis(_cfg(stub=True), "/repo", RepoProfile())
    stub = next(p for p in probes if "stub" in p.label)
    assert stub.status == "ok" and stub.note == "stub 1.2.3"


def test_configured_off_tools_are_listed_as_disabled(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    probes = probe_static_analysis(_cfg(stub=False), "/repo", RepoProfile())
    stub = next(p for p in probes if "stub" in p.label)
    assert stub.status == "warn" and "disabled" in stub.note


def test_unconfigured_tools_are_summarized_on_one_line(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "other", StubAnalyzer)
    probes = probe_static_analysis(_cfg(), "/repo", RepoProfile())
    summary = next(p for p in probes if "not configured" in p.label)
    assert "other" in summary.note and summary.status == "warn"


def test_unknown_version_line_still_counts_as_available(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr(StaticAnalyzer, "version", lambda self, ctx: "")
    probes = probe_static_analysis(_cfg(stub=True), "/repo", RepoProfile())
    stub = next(p for p in probes if "stub" in p.label)
    assert stub.status == "ok" and stub.note == "available"


def test_no_registered_analyzers_is_informational_not_a_warning(monkeypatch):
    # SA-1 ships an empty MODULES list, so doctor must still say something
    # about the layer without turning a fully-working install into a warning.
    monkeypatch.setattr(registry, "REGISTRY", {})
    probes = probe_static_analysis(_cfg(semgrep=True), "/repo", RepoProfile())
    assert [p.status for p in probes] == ["ok"]
    assert "no analyzers registered" in probes[0].note


@pytest.mark.parametrize("status", ["ok", "warn"])
def test_probe_is_immutable(status):
    from ai_review.static_analysis.doctor import Probe
    probe = Probe("label", status, "note")
    with pytest.raises(Exception):
        probe.label = "other"
