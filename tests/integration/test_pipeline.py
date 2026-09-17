"""Integration tests for the pipeline composition root (real git repos)."""
import json
import subprocess

import pytest

from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import RawLLMResponse, ReviewResult
from ai_review.parser import ParseError
from ai_review.pipeline import build_pipeline
from ai_review.providers.base import LlamaServerNotFound, ProviderError
from ai_review.providers.provider_bundle import StaticProvider
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.normalizer import make_finding
from ai_review.static_analysis.runner import ToolRun

GOOD = '{"decision": "PASS", "summary": "ok", "issues": []}'
BAD = '{"decision": "PASS"'
TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789"

GHOST = json.dumps({
    "decision": "WARN",
    "summary": "ghost issue",
    "issues": [{
        "severity": "LOW", "category": "OTHER", "file": "ghost.py", "line": 1,
        "title": "ghost", "description": "not staged", "evidence": "x",
        "recommendation": "y", "confidence": 0.9, "is_pre_existing": False,
    }],
})


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "T")
    return tmp_path


class DeadProvider:
    """Provider whose transport always fails with the given exception."""

    name = "dead"

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def send(self, payload, system_extra=""):
        self.calls += 1
        raise self.exc


class RecordingProvider:
    """Captures every payload, always answers a conforming PASS."""

    name = "recording"

    def __init__(self):
        self.payloads = []

    def send(self, payload, system_extra=""):
        self.payloads.append(payload)
        return RawLLMResponse(text=GOOD, provider=self.name, endpoint="rec",
                              duration_s=0.0)


# --- brief's three core tests -------------------------------------------------


def test_pipeline_pass_with_static_provider(repo):
    (repo / "go.mod").write_text("module x\n")
    (repo / "main.go").write_text("package main\nfunc main() {}\n")
    _git(repo, "add", "-A")
    cfg = AppConfig()
    pipe = build_pipeline(str(repo), cfg, provider=StaticProvider([GOOD]))
    result = pipe.run()
    assert result.decision == "PASS"
    assert result.issues == []


def test_pipeline_secret_scan_blocks_offline(repo):
    (repo / "x.py").write_text(f'token="{TOKEN}"\n')
    _git(repo, "add", "-A")
    cfg = AppConfig()
    pipe = build_pipeline(str(repo), cfg, provider=StaticProvider([GOOD]))
    result = pipe.run()
    assert result.decision == "BLOCK"
    assert any(f.hard_block for f in result.issues)


def test_pipeline_dry_run_no_provider_call(repo):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    called = []

    class NeverProvider:
        def send(self, payload):
            called.append(1)
            raise AssertionError("provider must not be called in dry-run")

    pipe = build_pipeline(str(repo), AppConfig(), provider=NeverProvider(), dry_run=True)
    plan = pipe.run()
    assert called == []
    assert "git diff --cached" in plan


# --- LLM-unavailable failure paths ---------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [LlamaServerNotFound("no server"), ProviderError("timeout"), ParseError("bad json")],
)
def test_llm_unavailable_warns(repo, exc):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    pipe = build_pipeline(str(repo), AppConfig(), provider=DeadProvider(exc))
    result = pipe.run()
    assert isinstance(result, ReviewResult)
    assert result.decision == "WARN"
    assert "skipped" in result.summary.lower()
    assert result.issues == []
    assert pipe.last_session is not None


def test_llm_unavailable_block_policy(repo):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    cfg = AppConfig()
    cfg.failure_policy.on_llm_unavailable = "block"
    pipe = build_pipeline(str(repo), cfg,
                          provider=DeadProvider(LlamaServerNotFound("down")))
    result = pipe.run()
    assert result.decision == "BLOCK"
    assert "failure_policy=block" in result.summary


def test_llm_unavailable_allow_policy(repo):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    cfg = AppConfig()
    cfg.failure_policy.on_llm_unavailable = "allow"
    pipe = build_pipeline(str(repo), cfg,
                          provider=DeadProvider(ProviderError("boom")))
    result = pipe.run()
    assert result.decision == "PASS"
    assert "failure_policy=allow" in result.summary


def test_offline_with_secret_still_blocks(repo):
    (repo / "x.py").write_text(f'token="{TOKEN}"\n')
    _git(repo, "add", "-A")
    pipe = build_pipeline(str(repo), AppConfig(),
                          provider=DeadProvider(LlamaServerNotFound("down")))
    result = pipe.run()
    assert result.decision == "BLOCK"
    assert any(f.hard_block for f in result.issues)


# --- corrective retry paths ------------------------------------------------------


def test_bad_json_then_good_corrective(repo):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    pipe = build_pipeline(str(repo), AppConfig(),
                          provider=StaticProvider([BAD, GOOD]))
    result = pipe.run()
    assert result.decision == "PASS"
    assert pipe.last_session.attempts == 2
    assert pipe.last_session.corrective_used is True


def test_review_unavailable_maps_to_failure_policy(repo):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    pipe = build_pipeline(str(repo), AppConfig(),
                          provider=StaticProvider([BAD, BAD]))
    result = pipe.run()
    assert result.decision == "WARN"
    assert "skipped" in result.summary.lower()
    assert pipe.last_session.corrective_used is True
    assert pipe.last_session.attempts == 2


# --- metadata, hooks, dry-run duality ---------------------------------------------


def test_commit_meta_reports_repo_and_branch(repo):
    pipe = build_pipeline(str(repo), AppConfig(), provider=StaticProvider([]))
    meta = pipe.commit_meta(str(repo))
    assert meta["repo"] == repo.name
    assert isinstance(meta["branch"], str) and meta["branch"]


def test_select_only_stub_returns_none(repo):
    pipe = build_pipeline(str(repo), AppConfig(), provider=StaticProvider([]))
    assert pipe.select_only("go") is None


def test_dry_run_method_matches_flagged_run(repo):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    flagged = build_pipeline(str(repo), AppConfig(),
                             provider=StaticProvider([]), dry_run=True)
    plan_flag = flagged.run()
    assert isinstance(plan_flag, str)
    method = build_pipeline(str(repo), AppConfig(), provider=StaticProvider([]))
    plan_method = method.dry_run()
    assert plan_flag == plan_method
    assert "git diff --cached" in plan_method


def test_run_stores_meta(repo):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    pipe = build_pipeline(str(repo), AppConfig(), provider=StaticProvider([GOOD]))
    result = pipe.run()
    meta = pipe.opts.meta
    assert meta["repo"] == repo.name
    assert isinstance(meta["branch"], str) and meta["branch"]
    assert meta["files"] == 1
    assert meta["added"] == 1 and meta["removed"] == 0
    assert "Go" in meta["detected"]
    assert meta["duration_s"] is not None
    assert meta["checks"] == result.checks == []


def test_build_pipeline_default_cfg(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    pipe = build_pipeline(str(repo))
    assert isinstance(pipe.opts.cfg, AppConfig)
    assert pipe.opts.provider is None
    assert pipe.opts.dry_run is False
    assert pipe.opts.verbose is False
    assert pipe.opts.select_only is None


# --- redaction and merge-before-validate ordering -----------------------------------


def test_secrets_redacted_from_provider_payload(repo):
    (repo / "x.py").write_text(f'token="{TOKEN}"\n')
    _git(repo, "add", "-A")
    cfg = AppConfig()
    cfg.security.excluded_files = ["x.py"]  # isolate redaction from the scan gate
    rec = RecordingProvider()
    pipe = build_pipeline(str(repo), cfg, provider=rec)
    result = pipe.run()
    assert result.decision == "PASS"
    assert rec.payloads
    assert all("[REDACTED:" in p.user for p in rec.payloads)
    assert all(TOKEN not in p.user for p in rec.payloads)


def test_redaction_disabled_sends_raw(repo):
    (repo / "x.py").write_text(f'token="{TOKEN}"\n')
    _git(repo, "add", "-A")
    cfg = AppConfig()
    cfg.security.redact_secrets = False
    cfg.security.excluded_files = ["x.py"]
    rec = RecordingProvider()
    result = build_pipeline(str(repo), cfg, provider=rec).run()
    assert result.decision == "PASS"
    assert rec.payloads
    assert any(TOKEN in p.user for p in rec.payloads)
    assert all("[REDACTED:" not in p.user for p in rec.payloads)


def test_llm_findings_validated_and_security_merged_first(repo):
    (repo / "x.py").write_text(f'token="{TOKEN}"\n')
    _git(repo, "add", "-A")
    pipe = build_pipeline(str(repo), AppConfig(), provider=StaticProvider([GHOST]))
    result = pipe.run()
    assert result.decision == "BLOCK"
    # ghost.py finding dropped by validate_findings; only staged-anchored
    # security findings survive (github + password patterns -> two findings).
    assert result.issues
    assert all(f.hard_block for f in result.issues)
    assert all(f.file == "x.py" for f in result.issues)
    assert not any(f.file == "ghost.py" for f in result.issues)


def test_dry_run_reports_redaction_disabled(repo):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    cfg = AppConfig()
    cfg.security.redact_secrets = False
    pipe = build_pipeline(str(repo), cfg, provider=NeverProvider(), dry_run=True)
    plan = pipe.run()
    assert "redact secrets (disabled)" in plan


class NeverProvider:
    def send(self, payload):
        raise AssertionError("provider must not be called")


def test_progress_callback_fires_before_llm_call(repo):
    (repo / "go.mod").write_text("module x\n")
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    seen = []
    pipe = build_pipeline(str(repo), AppConfig(),
                          provider=StaticProvider([GOOD]),
                          progress=seen.append)
    result = pipe.run()
    assert result.decision == "PASS"
    assert len(seen) == 1
    assert seen[0]["files"] == 2
    assert seen[0]["provider"] == "llamacpp"
    assert seen[0]["model"] == "auto"
    assert "timeout_seconds" in seen[0]


# --- deterministic resolution gate (missing imports) ------------------------------

WEB_PHP = """<?php

use Illuminate\\Support\\Facades\\Route;

Route::get('/', function () {
    $user = User::all();
    return view('welcome');
});
"""

USER_PHP = """<?php

namespace App\\Models;

class User
{
    protected $table = 'users';
}
"""


def _laravel_repo(repo, route_body=WEB_PHP):
    (repo / "composer.json").write_text('{"require": {"laravel/framework": "^10"}}')
    (repo / "app" / "Models").mkdir(parents=True)
    (repo / "app" / "Models" / "User.php").write_text(USER_PHP)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    (repo / "routes").mkdir()
    (repo / "routes" / "web.php").write_text(route_body)
    _git(repo, "add", "routes/web.php")


def test_missing_import_blocks_a_passing_llm(repo):
    _laravel_repo(repo)
    pipe = build_pipeline(str(repo), AppConfig(), provider=StaticProvider([GOOD]))
    result = pipe.run()
    assert result.decision == "BLOCK"
    unresolved = [f for f in result.issues if f.title == "Unresolved reference: User"]
    assert len(unresolved) == 1
    assert unresolved[0].hard_block is True
    assert unresolved[0].severity == "HIGH"
    assert "use App\\Models\\User;" in unresolved[0].recommendation


def test_missing_import_blocks_with_llm_unavailable(repo):
    _laravel_repo(repo)
    pipe = build_pipeline(str(repo), AppConfig(),
                          provider=DeadProvider(LlamaServerNotFound("down")))
    result = pipe.run()
    assert result.decision == "BLOCK"


def test_imported_model_passes(repo):
    _laravel_repo(repo, route_body=WEB_PHP.replace(
        "use Illuminate\\Support\\Facades\\Route;",
        "use App\\Models\\User;\nuse Illuminate\\Support\\Facades\\Route;",
    ))
    result = build_pipeline(str(repo), AppConfig(),
                            provider=StaticProvider([GOOD])).run()
    assert result.decision == "PASS"
    assert result.issues == []


def test_resolution_gate_can_be_disabled(repo):
    _laravel_repo(repo)
    cfg = AppConfig()
    cfg.resolution.enabled = False
    result = build_pipeline(str(repo), cfg, provider=StaticProvider([GOOD])).run()
    assert result.decision == "PASS"


def test_dry_run_plan_lists_resolution_gate(repo):
    _laravel_repo(repo)
    plan = build_pipeline(str(repo), AppConfig(), provider=NeverProvider(),
                          dry_run=True).run()
    assert "resolution gate" in plan


def test_progress_not_called_on_dry_run(repo):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    seen = []
    pipe = build_pipeline(str(repo), AppConfig(), provider=NeverProvider(),
                          dry_run=True, progress=seen.append)
    plan = pipe.run()
    assert "git diff --cached" in plan
    assert seen == []


# --- static-analysis layer (design D1/D5/D7) ------------------------------------


def _stage(root, name, content="x = 1\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(root, "add", name)
    return path


class FakeStaticAnalyzer(StaticAnalyzer):
    """Registered stand-in for an installed tool (no binary needed offline)."""
    name = "fake-static"
    executable = "fake-static"
    ok_exit_codes = (0,)

    def build_argv(self, exe, ctx):
        return [exe]

    def parse(self, run, ctx):
        return [make_finding(tool=self.name, severity="HIGH", rule_id="fake.rule",
                             file="app.py", line=1, message="Static fake finding.")]


@pytest.fixture
def static_enabled(monkeypatch):
    """Enable the fake analyzer end to end: registered + resolvable + fake run."""
    monkeypatch.setitem(registry.REGISTRY, "fake-static", FakeStaticAnalyzer)
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: ToolRun(argv=list(argv), status="run",
                                                   exit_code=0, stdout="[]"))
    cfg = AppConfig()
    cfg.static_analysis.tools = {"fake-static": StaticAnalysisToolConfig()}
    return cfg


def test_static_findings_reach_the_llm_payload(repo, static_enabled):
    _stage(repo, "app.py")
    rec = RecordingProvider()
    result = build_pipeline(str(repo), static_enabled, provider=rec).run()
    assert result.static is not None
    assert [f.tool for f in result.static.findings] == ["fake-static"]
    assert result.static.tools[0].status == "run"
    assert "# Static Analysis Findings" in rec.payloads[0].user
    assert "fake-static" in rec.payloads[0].user
    assert "Static fake finding." in rec.payloads[0].user


def test_static_findings_do_not_gate_the_policy(repo, static_enabled):
    _stage(repo, "app.py")
    result = build_pipeline(str(repo), static_enabled,
                            provider=StaticProvider([GOOD])).run()
    assert result.decision == "PASS"          # HIGH tool finding, not a hard block
    assert result.issues == []


def test_static_analysis_disabled_keeps_the_old_prompt(repo, static_enabled):
    _stage(repo, "app.py")
    static_enabled.static_analysis.enabled = False
    rec = RecordingProvider()
    result = build_pipeline(str(repo), static_enabled, provider=rec).run()
    assert result.static is None
    assert "# Static Analysis Findings" not in rec.payloads[0].user


def test_static_only_never_calls_the_provider(repo, static_enabled):
    _stage(repo, "app.py")
    pipe = build_pipeline(str(repo), static_enabled,
                          provider=DeadProvider(LlamaServerNotFound("must not be called")),
                          static_only=True)
    result = pipe.run()
    assert pipe.last_session is None
    assert result.static is not None
    assert result.decision == "PASS"
    assert "skipped" in result.summary.lower()
    assert result.static.findings[0].tool == "fake-static"


def test_static_only_still_blocks_on_a_staged_secret(repo, static_enabled):
    _stage(repo, "creds.py", f'token="{TOKEN}"\n')
    result = build_pipeline(str(repo), static_enabled,
                            provider=DeadProvider(LlamaServerNotFound("x")),
                            static_only=True).run()
    assert result.decision == "BLOCK"
    assert any(f.hard_block for f in result.issues)


def test_dry_run_separates_planned_from_not_applicable(repo, static_enabled, monkeypatch):
    """A tool that cannot run on this change set is never called "planned"."""

    class GoOnlyAnalyzer(FakeStaticAnalyzer):
        name = "fake-go"
        languages = ("Go",)

    monkeypatch.setitem(registry.REGISTRY, "fake-go", GoOnlyAnalyzer)
    static_enabled.static_analysis.tools["fake-go"] = StaticAnalysisToolConfig()
    _stage(repo, "app.py")
    plan = build_pipeline(str(repo), static_enabled,
                          provider=NeverProvider(), dry_run=True).run()
    assert "planned: fake-static — skipped: fake-go" in plan


def test_dry_run_plans_static_analysis_without_running_it(repo, static_enabled,
                                                          monkeypatch):
    _stage(repo, "app.py")
    ran = []
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda *a, **kw: ran.append(a))
    plan = build_pipeline(str(repo), static_enabled,
                          provider=NeverProvider(), dry_run=True).run()
    assert "static analysis (planned: fake-static)" in plan
    assert "[NOT run in dry-run]" in plan
    assert ran == []                        # no tool process, no provider call
