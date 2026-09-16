"""Unit tests for doctor.py: every probe, status and exit-code contract.

All tests run offline and hermetically: the LLM probe is monkeypatched (no
real endpoint is ever dialed), HOME is isolated so the user config layer is
empty, and repos are throwaway ``git init`` temp directories driven with
subprocess list-args (never shell=True).
"""
import shutil
import subprocess

import httpx
import pytest

from ai_review.doctor import run_doctor
from ai_review.hooks import install_hook


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _line(lines, label):
    return next(line for line in lines if label in line)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Isolate the user config layer: doctor must see defaults only.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AI_REVIEW_ORG_CONFIG", raising=False)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "go.mod").write_text("module x\n")
    return tmp_path


@pytest.fixture
def llm_unreachable(repo, monkeypatch):
    """Nothing is listening on the configured endpoint (deterministic)."""

    def _refused(url, **kwargs):
        raise httpx.ConnectError(f"Connection refused: {url}")

    monkeypatch.setattr("ai_review.doctor.httpx.get", _refused)


# -- happy path ----------------------------------------------------------------


def test_doctor_reports_ok_lines(repo, llm_unreachable):
    rc, lines = run_doctor(str(repo))
    text = "\n".join(lines)
    assert "Git" in text and "Technology detection" in text and "Prompt" in text
    assert any("Secret redaction" in line for line in lines)
    assert "LLM" in text
    # Every probe line is present.
    for label in ("Git binary", "Git repository", "Configuration",
                  "Technology detection", "Prompt files", "Secret redaction",
                  "Git hook", "LLM endpoint"):
        assert any(label in line for line in lines), label
    # Drift pin: the prompt dir must resolve to the shipped asset directory,
    # not the prompts.py package dir (a ✗ here means the bug regressed).
    assert _line(lines, "Prompt files").startswith("✓")
    assert "Go" in _line(lines, "Technology detection")
    # No errors: hook missing + LLM unreachable are warnings only.
    assert rc == 0
    assert "System ready" in text
    assert "System not ready" not in text


def test_doctor_all_green_is_system_ready(repo, monkeypatch):
    install_hook(str(repo))

    def _ok(url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr("ai_review.doctor.httpx.get", _ok)
    rc, lines = run_doctor(str(repo))
    assert rc == 0
    assert lines[-1] == "System ready."
    assert _line(lines, "Git hook").startswith("✓")
    assert "installed by ai-review" in _line(lines, "Git hook")
    assert _line(lines, "LLM endpoint").startswith("✓")


# -- LLM endpoint probe ---------------------------------------------------------


def test_doctor_llm_unavailable_is_warn_not_error(repo, llm_unreachable):
    rc, lines = run_doctor(str(repo))
    llm_line = _line(lines, "LLM endpoint")
    assert llm_line.startswith("⚠")
    assert "server unreachable" in llm_line
    assert "llamacpp" in llm_line
    assert rc == 0


def test_doctor_llm_http_200_is_ok(repo, monkeypatch):
    seen = {}

    def _ok(url, **kwargs):
        seen.update(url=url, kwargs=kwargs)
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr("ai_review.doctor.httpx.get", _ok)
    rc, lines = run_doctor(str(repo))
    assert _line(lines, "LLM endpoint").startswith("✓")
    assert rc == 0
    # Probe contract: GET {endpoint}/v1/models within 3 seconds, ignoring
    # proxy env vars so localhost probes cannot be hijacked.
    assert seen["url"] == "http://127.0.0.1:8080/v1/models"
    assert seen["kwargs"] == {"timeout": 3.0, "trust_env": False}


def test_doctor_llm_http_500_is_warn(repo, monkeypatch):
    def _fail(url, **kwargs):
        return httpx.Response(500, request=httpx.Request("GET", url))

    monkeypatch.setattr("ai_review.doctor.httpx.get", _fail)
    rc, lines = run_doctor(str(repo))
    llm_line = _line(lines, "LLM endpoint")
    assert llm_line.startswith("⚠")
    assert "HTTP 500" in llm_line
    assert rc == 0


# -- error paths (rc == 1) -------------------------------------------------------


def test_doctor_outside_git_repo_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AI_REVIEW_ORG_CONFIG", raising=False)

    def _refused(url, **kwargs):
        raise OSError("no network in tests")

    monkeypatch.setattr("ai_review.doctor.httpx.get", _refused)
    rc, lines = run_doctor(str(tmp_path))
    assert _line(lines, "Git repository").startswith("✗")
    assert _line(lines, "System not ready").startswith("System not ready")
    assert rc == 1


def test_doctor_missing_git_binary_is_error(repo, llm_unreachable, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    rc, lines = run_doctor(str(repo))
    assert _line(lines, "Git binary").startswith("✗")
    assert "System not ready" in "\n".join(lines)
    assert rc == 1


def test_doctor_config_error_skips_llm(repo, llm_unreachable, monkeypatch):
    def _boom(repo_dir):
        raise RuntimeError("bad yaml")

    monkeypatch.setattr("ai_review.doctor.load_config_for_repo", _boom)
    rc, lines = run_doctor(str(repo))
    assert _line(lines, "Configuration").startswith("✗")
    assert "bad yaml" in _line(lines, "Configuration")
    llm_line = _line(lines, "LLM endpoint")
    assert llm_line.startswith("✗")
    assert "skipped (no config)" in llm_line
    assert rc == 1


def test_doctor_detection_error(repo, llm_unreachable, monkeypatch):
    def _boom(change_paths, repo_dir):
        raise RuntimeError("fs exploded")

    monkeypatch.setattr("ai_review.doctor.detect", _boom)
    rc, lines = run_doctor(str(repo))
    assert _line(lines, "Technology detection").startswith("✗")
    assert rc == 1


def test_doctor_no_technology_detected_is_warn(repo, llm_unreachable):
    (repo / "go.mod").unlink()
    rc, lines = run_doctor(str(repo))
    det_line = _line(lines, "Technology detection")
    assert det_line.startswith("⚠")
    assert "no known technology detected" in det_line
    assert rc == 0


def test_doctor_prompt_files_missing(repo, llm_unreachable, tmp_path, monkeypatch):
    empty = tmp_path / "no-assets"
    empty.mkdir()
    monkeypatch.setattr("ai_review.doctor.default_prompt_dir", lambda: str(empty))
    rc, lines = run_doctor(str(repo))
    prompt_line = _line(lines, "Prompt files")
    assert prompt_line.startswith("✗")
    assert "missing:" in prompt_line
    assert "system.md" in prompt_line and "review.md" in prompt_line
    assert rc == 1


def test_doctor_redaction_selftest_failure(repo, llm_unreachable, monkeypatch):
    monkeypatch.setattr("ai_review.doctor.redact_text",
                        lambda text, patterns=None: (text, []))
    rc, lines = run_doctor(str(repo))
    red_line = _line(lines, "Secret redaction")
    assert red_line.startswith("✗")
    assert "self-test failed" in red_line
    assert rc == 1


# -- hook install states ----------------------------------------------------------


def test_doctor_hook_states(repo, llm_unreachable):
    hooks = repo / ".git" / "hooks"
    # None installed -> warn.
    rc, lines = run_doctor(str(repo))
    hook_line = _line(lines, "Git hook")
    assert hook_line.startswith("⚠")
    assert "not installed" in hook_line
    # Ours -> ok.
    install_hook(str(repo))
    rc, lines = run_doctor(str(repo))
    hook_line = _line(lines, "Git hook")
    assert hook_line.startswith("✓")
    # Foreign hook -> warn, never an error.
    (hooks / "pre-commit").write_text("#!/bin/sh\necho other tool\n")
    rc, lines = run_doctor(str(repo))
    hook_line = _line(lines, "Git hook")
    assert hook_line.startswith("⚠")
    assert "not ours" in hook_line
    assert rc == 0
