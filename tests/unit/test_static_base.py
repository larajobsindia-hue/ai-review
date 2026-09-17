"""The analyzer template method: availability, exit codes, fault isolation."""
import os

from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import ProfileEntry, RepoProfile, StagedChange
from ai_review.static_analysis import runner
from ai_review.static_analysis.base import StaticAnalyzer, tool_enabled
from ai_review.static_analysis.context import build_context
from ai_review.static_analysis.normalizer import make_finding

PY = RepoProfile(languages=[ProfileEntry("Python", 0.9, [])])


class FakeAnalyzer(StaticAnalyzer):
    name = "fake"
    executable = "fake-tool"
    languages = ("Python",)
    extensions = (".py",)
    ok_exit_codes = (0, 1)

    def build_argv(self, exe, ctx):
        return [exe, "--json", *ctx.changed_paths(self.extensions)]

    def parse(self, run, ctx):
        return [make_finding(tool=self.name, severity="LOW", file="a.py", line=1,
                             message=run.stdout.strip())]


class BoomParser(FakeAnalyzer):
    name = "boom"

    def parse(self, run, ctx):
        raise ValueError("unparseable")


def _ctx(tmp_path, *tools, profile=PY):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = AppConfig()
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig() for name in tools}
    return build_context(str(tmp_path), cfg,
                         [StagedChange(path="a.py", status="modified")], profile)


def test_supports_requires_explicit_enablement_and_technology(tmp_path):
    analyzer = FakeAnalyzer()
    assert analyzer.supports(_ctx(tmp_path)) is False            # not configured
    assert analyzer.supports(_ctx(tmp_path, "fake")) is True
    assert analyzer.supports(_ctx(tmp_path, "other")) is False
    js = RepoProfile(languages=[ProfileEntry("JavaScript", 0.9, [])])
    assert analyzer.supports(_ctx(tmp_path, "fake", profile=js)) is False


def test_resolve_prefers_project_local_binary(tmp_path):
    local = tmp_path / "node_modules" / ".bin"
    local.mkdir(parents=True)
    exe = local / "fake-tool"
    exe.write_text("#!/bin/sh\n")
    os.chmod(exe, 0o755)
    analyzer = type("Local", (FakeAnalyzer,),
                    {"local_paths": ("node_modules/.bin/fake-tool",)})()
    ctx = _ctx(tmp_path, "fake")
    assert analyzer.resolve(ctx) == str(exe)
    assert analyzer.is_available(ctx) is True


def test_unavailable_tool_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: None)
    result = FakeAnalyzer().analyze(_ctx(tmp_path, "fake"))
    assert result.status == "unavailable" and result.findings == []
    assert "not found" in result.error


def test_ok_exit_codes_cover_linters_that_report_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    # NOTE: base.py imports ``run_tool`` by name, so tests patch it there.
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(
                            argv=argv, status="run", exit_code=1, stdout="problem\n"))
    result = FakeAnalyzer().analyze(_ctx(tmp_path, "fake"))
    assert result.status == "run" and result.exit_code == 1
    assert result.findings[0].title == "problem"


def test_unexpected_exit_code_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(
                            argv=argv, status="run", exit_code=2, stdout="",
                            stderr="fatal: bad config"))
    result = FakeAnalyzer().analyze(_ctx(tmp_path, "fake"))
    assert result.status == "failed" and "fatal" in result.error and result.findings == []


def test_timeout_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(
                            argv=argv, status="timeout", error="timed out after 120s"))
    result = FakeAnalyzer().analyze(_ctx(tmp_path, "fake"))
    assert result.status == "failed" and "timed out" in result.error


def test_parser_exception_becomes_failed_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(
                            argv=argv, status="run", exit_code=0, stdout="not json"))
    result = BoomParser().analyze(_ctx(tmp_path, "boom"))
    assert result.status == "failed" and "unparseable" in result.error


class SqlOnly(FakeAnalyzer):
    """Analyzer that returns no argv when it has no relevant target."""
    name = "sqlonly"
    extensions = (".sql",)

    def build_argv(self, exe, ctx):
        targets = ctx.changed_paths(self.extensions)
        return [exe, "--json", *targets] if targets else []


def test_no_targets_is_skipped_without_running_anything(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda *a, **kw: called.append(1))
    result = SqlOnly().analyze(_ctx(tmp_path, "sqlonly"))     # only a .py is staged
    assert result.status == "skipped" and called == []
    assert result.error == "no targets"


def test_tool_enabled_reads_the_opt_in_map():
    cfg = AppConfig()
    assert tool_enabled(cfg.static_analysis, "ruff") is False
    assert tool_enabled(None, "ruff") is False


def test_analyze_passes_the_configured_timeout_to_the_runner(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return runner.ToolRun(argv=list(argv), status="run", exit_code=0, stdout="ok")

    monkeypatch.setattr("ai_review.static_analysis.base.run_tool", fake_run)
    ctx = _ctx(tmp_path, "fake")
    ctx.cfg.timeout = 7
    FakeAnalyzer().analyze(ctx)
    assert seen["timeout"] == 7
    assert seen["cwd"] == str(tmp_path)
