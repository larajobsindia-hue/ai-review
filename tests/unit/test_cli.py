"""Unit tests for cli.py: flag wiring, exit codes, formats, hook commands.

All tests run offline: the provider layer is monkeypatched so no real LLM is
ever contacted. Repos are throwaway ``git init`` temp directories driven with
subprocess list-args (never shell=True).
"""
import json
import subprocess
from types import SimpleNamespace

import pytest

from ai_review.cli import EXIT_BLOCK, EXIT_ERROR, EXIT_OK, _coerce, _parse_dotted, main
from ai_review.models import ReviewResult
from ai_review.providers.base import LlamaServerNotFound


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "T")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _stage(root, name, content="x = 1\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(root, "add", name)
    return path


class FailingProvider:
    """Provider stub whose LLM call always fails (server unreachable)."""

    name = "failing"

    def send(self, payload, system_extra=""):
        raise LlamaServerNotFound("cannot connect: test stub")


@pytest.fixture
def failing_provider(monkeypatch):
    monkeypatch.setattr("ai_review.providers.make_provider", lambda cfg: FailingProvider())


# -- basics -----------------------------------------------------------------


def test_version_exits_zero(repo):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0


def test_exit_code_constants():
    assert (EXIT_OK, EXIT_BLOCK, EXIT_ERROR) == (0, 1, 2)


# -- review run -------------------------------------------------------------


def test_warn_path_exit_zero_and_skipped_summary(repo, failing_provider, capsys):
    _stage(repo, "app.py")
    rc = main(["--staged"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "RESULT: WARNING" in out
    assert "AI review was skipped" in out


def test_block_exit_one_offline_plus_secret(repo, failing_provider):
    _stage(repo, "creds.py", 'token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"\n')
    assert main(["--staged"]) == EXIT_BLOCK


def test_verbose_reports_findings_on_block(repo, failing_provider, capsys):
    _stage(repo, "creds.py", 'token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"\n')
    rc = main(["--staged", "--verbose"])
    err = capsys.readouterr().err
    assert rc == EXIT_BLOCK
    # the token line matches both the github and the password/key patterns
    assert "[verbose] 2 finding(s), policy=BLOCK" in err


def test_pipeline_error_exit_two(repo, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("ai_review.cli.build_pipeline", boom)
    rc = main(["--staged"])
    err = capsys.readouterr().err
    assert rc == EXIT_ERROR
    assert "error: boom" in err


def test_dry_run_prints_plan_and_never_builds_provider(repo, monkeypatch, capsys):
    _stage(repo, "app.py")

    def _boom(cfg):
        raise AssertionError("make_provider must not be called on --dry-run")

    monkeypatch.setattr("ai_review.providers.make_provider", _boom)
    rc = main(["--dry-run"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "git diff --cached" in out
    assert "dry-run" in out


def test_format_json(repo, failing_provider, capsys):
    _stage(repo, "app.py")
    rc = main(["--format", "json"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    parsed = json.loads(out)
    assert parsed["decision"] == "WARN"
    assert "skipped" in parsed["summary"]


def test_format_markdown(repo, failing_provider, capsys):
    _stage(repo, "app.py")
    rc = main(["--format", "markdown"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert out.startswith("# AI Review")


# -- flag wiring -------------------------------------------------------------


class RecordingPipe:
    opts = SimpleNamespace(meta={"repo": "r", "branch": "b"})

    def run(self):
        return ReviewResult(decision="PASS", summary="ok")


def test_config_overrides_reach_build_pipeline(repo, monkeypatch, capsys):
    captured = {}

    def fake_build(repo_dir, cfg, *, provider=None, dry_run=False, verbose=False):
        captured.update(repo_dir=repo_dir, cfg=cfg, provider=provider,
                        dry_run=dry_run, verbose=verbose)
        return RecordingPipe()

    monkeypatch.setattr("ai_review.cli.build_pipeline", fake_build)
    monkeypatch.setattr("ai_review.providers.make_provider", lambda cfg: object())
    rc = main([
        "--config", "llm.endpoint=http://x:zz",
        "--config", "policy.minimum_confidence_to_block=0.9",
        "--provider", "openai_compatible",
        "--endpoint", "http://y:1",
        "--verbose",
    ])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    # the --endpoint flag is applied after --config, so it wins
    assert captured["cfg"].llm.endpoint == "http://y:1"
    assert captured["cfg"].policy.minimum_confidence_to_block == 0.9
    assert captured["cfg"].llm.provider == "openai_compatible"
    assert captured["provider"] is not None
    assert captured["verbose"] is True
    assert "RESULT: PASS" in out


def test_invalid_config_exit_two(repo, capsys):
    rc = main(["--config", "llm.timeout_seconds=abc"])
    err = capsys.readouterr().err
    assert rc == EXIT_ERROR
    assert "invalid configuration" in err


# -- hook management ---------------------------------------------------------


def test_install_and_uninstall_hook_via_cli(repo, capsys):
    hook = repo / ".git" / "hooks" / "pre-commit"
    assert main(["--install-hook"]) == EXIT_OK
    assert hook.is_file()
    assert main(["--uninstall-hook"]) == EXIT_OK
    assert not hook.exists()
    out = capsys.readouterr().out
    assert "installed" in out
    assert "removed" in out


def test_install_hook_failure_exit_two(repo, monkeypatch, capsys):
    def boom(repo_dir):
        raise OSError("disk full")

    monkeypatch.setattr("ai_review.cli.install_hook", boom)
    rc = main(["--install-hook"])
    err = capsys.readouterr().err
    assert rc == EXIT_ERROR
    assert "failed to install hook" in err


def test_uninstall_hook_failure_exit_two(repo, monkeypatch, capsys):
    def boom(repo_dir):
        raise OSError("disk full")

    monkeypatch.setattr("ai_review.cli.uninstall_hook", boom)
    rc = main(["--uninstall-hook"])
    err = capsys.readouterr().err
    assert rc == EXIT_ERROR
    assert "failed to uninstall hook" in err


# -- _parse_dotted / _coerce --------------------------------------------------


def test_parse_dotted_nested_dicts():
    assert _parse_dotted(["a.b.c=1", "a.b.d=true", "a.e=x", "f=2.5"]) == {
        "a": {"b": {"c": 1, "d": True}, "e": "x"},
        "f": 2.5,
    }


def test_parse_dotted_value_may_contain_equals():
    assert _parse_dotted(["llm.endpoint=http://x?a=b"]) == {"llm": {"endpoint": "http://x?a=b"}}


def test_parse_dotted_empty_value_is_string():
    assert _parse_dotted(["llm.api_key="]) == {"llm": {"api_key": ""}}


@pytest.mark.parametrize("raw,expected", [
    ("true", True),
    ("False", False),
    ("42", 42),
    ("-3", -3),
    ("2.5", 2.5),
    ("abc", "abc"),
    ("", ""),
    ("1.2.3", "1.2.3"),
])
def test_coerce(raw, expected):
    assert _coerce(raw) == expected
    assert isinstance(_coerce(raw), type(expected))
