"""The static-analysis entry points: aggregation, isolation, prompt formatting."""
import pytest

from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import AnalyzerResult, ProfileEntry, RepoProfile, StagedChange
from ai_review.static_analysis import (format_static_findings, plan_static_analysis,
                                       registry, run_static_analysis)
from ai_review.static_analysis.base import StaticAnalysisError, StaticAnalyzer
from ai_review.static_analysis.context import build_context
from ai_review.static_analysis.normalizer import make_finding

PROFILE = RepoProfile(languages=[ProfileEntry("Python", 0.9, [])])


class StubAnalyzer(StaticAnalyzer):
    """Analyzer with stubbed execution: no subprocess, fully offline."""
    name = "stub"
    executable = "stub"
    extensions = (".py",)

    def build_argv(self, exe, ctx):
        return [exe]

    def parse(self, run, ctx):
        return []

    def analyze(self, ctx):
        return AnalyzerResult(
            findings=[make_finding(tool=self.name, severity="HIGH", rule_id="stub.rule",
                                   file="a.py", line=1, message="Static stub finding.")],
            status="run", exit_code=1, duration_ms=7)


class BoomAnalyzer(StubAnalyzer):
    name = "boom"

    def analyze(self, ctx):
        raise RuntimeError("kaboom")


def _ctx(tmp_path, *tools, changes=None):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = AppConfig()
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig() for name in tools}
    return cfg, build_context(str(tmp_path), cfg, changes if changes is not None else
                              [StagedChange(path="a.py", status="modified")], PROFILE)


def test_run_aggregates_tools_and_findings(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path, "stub")
    summary = run_static_analysis(cfg.static_analysis, ctx)
    assert [t.name for t in summary.tools] == ["stub"]
    assert summary.tools[0].status == "run" and summary.tools[0].exit_code == 1
    assert len(summary.findings) == 1
    assert summary.findings[0].tool == "stub" and summary.findings[0].id
    assert summary.files_analyzed == 1
    assert summary.severity_counts()["HIGH"] == 1


def test_analyzer_crash_is_isolated_and_reported(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    monkeypatch.setitem(registry.REGISTRY, "boom", BoomAnalyzer)
    cfg, ctx = _ctx(tmp_path, "stub", "boom")
    summary = run_static_analysis(cfg.static_analysis, ctx)
    assert {t.name: t.status for t in summary.tools} == {"stub": "run", "boom": "failed"}
    assert "kaboom" in next(t.error for t in summary.tools if t.name == "boom")
    assert [f.tool for f in summary.findings] == ["stub"]


def test_unconfigured_tool_never_runs(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path)
    summary = run_static_analysis(cfg.static_analysis, ctx)
    assert summary.findings == [] and summary.tools == []


def test_fail_on_error_raises_only_when_opted_in(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "boom", BoomAnalyzer)
    cfg, ctx = _ctx(tmp_path, "boom")
    run_static_analysis(cfg.static_analysis, ctx)            # default: tolerant
    cfg.static_analysis.fail_on_error = True
    with pytest.raises(StaticAnalysisError) as excinfo:
        run_static_analysis(cfg.static_analysis, ctx)
    assert "boom" in str(excinfo.value)


def test_plan_static_analysis_executes_nothing(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path, "stub")
    planned = plan_static_analysis(cfg.static_analysis, ctx)
    assert planned.findings == []
    assert [t.status for t in planned.tools] == ["skipped"]
    assert planned.tools[0].error == "stub not found"        # not installed here

    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    available = plan_static_analysis(cfg.static_analysis, ctx)
    assert available.tools[0].error == "dry-run (not executed)"
    assert available.tools[0].status == "skipped"


def test_files_analyzed_counts_only_covered_staged_files(tmp_path, monkeypatch):
    (tmp_path / "b.js").write_text("let x = 1\n")
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path, "stub", changes=[
        StagedChange(path="a.py", status="modified"),
        StagedChange(path="b.js", status="modified"),
    ])
    assert run_static_analysis(cfg.static_analysis, ctx).files_analyzed == 1


def test_not_applicable_tool_is_reported_as_skipped(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path, "stub",
                    changes=[StagedChange(path="a.js", status="modified")])
    (tmp_path / "a.js").write_text("let x = 1\n")
    summary = run_static_analysis(cfg.static_analysis, ctx)
    assert summary.findings == []
    assert [(t.name, t.status) for t in summary.tools] == [("stub", "skipped")]


def test_format_static_findings_is_empty_without_findings(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path)
    assert format_static_findings(run_static_analysis(cfg.static_analysis, ctx)) == ""
    assert format_static_findings(None) == ""


def test_format_static_findings_reports_tools_and_ids(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path, "stub")
    summary = run_static_analysis(cfg.static_analysis, ctx)
    text = format_static_findings(summary)
    finding = summary.findings[0]
    assert text.startswith("1 finding(s) from 1 tool(s): stub")
    assert f"id {finding.id}" in text
    assert "a.py:1" in text
    assert "rule stub.rule" in text
    assert "Static stub finding." in text


def test_format_static_findings_orders_truncates_and_marks_omissions(tmp_path, monkeypatch):
    class Loud(StubAnalyzer):
        name = "loud"

        def analyze(self, ctx):
            return AnalyzerResult(
                findings=[make_finding(tool=self.name, severity=sev, file="a.py",
                                       line=i + 1, message="A very long message " * 40)
                          for i, sev in enumerate(["LOW", "CRITICAL", "MEDIUM", "HIGH"])],
                status="run")

    monkeypatch.setitem(registry.REGISTRY, "loud", Loud)
    cfg, ctx = _ctx(tmp_path, "loud")
    summary = run_static_analysis(cfg.static_analysis, ctx)
    assert len(summary.findings) == 4
    text = format_static_findings(summary, max_findings=2, max_message_chars=50)
    assert text.startswith("4 finding(s) from 1 tool(s): loud")
    assert text.index("[CRITICAL]") < text.index("[HIGH]")           # severity ordered
    assert "…" in text                                                # message truncated
    assert "[2 further finding(s) omitted from this list]" in text
    assert text.count("\n- [") == 2                                   # budget respected


def test_per_tool_finding_cap_is_applied(tmp_path, monkeypatch):
    class Flood(StubAnalyzer):
        name = "flood"

        def analyze(self, ctx):
            return AnalyzerResult(
                findings=[make_finding(tool=self.name, severity="LOW", file="a.py",
                                       line=i + 1, message=f"problem {i}")
                          for i in range(10)], status="run")

    monkeypatch.setitem(registry.REGISTRY, "flood", Flood)
    cfg, ctx = _ctx(tmp_path, "flood")
    cfg.static_analysis.max_findings = 3
    summary = run_static_analysis(cfg.static_analysis, ctx)
    assert len(summary.findings) == 3
