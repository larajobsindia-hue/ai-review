"""The subprocess boundary: argv lists, timeouts, and JSON shapes."""
import subprocess

import pytest

from ai_review.static_analysis import runner


def test_run_tool_success_captures_output(tmp_path):
    run = runner.run_tool(["python3", "-c", "print('hi')"], cwd=str(tmp_path), timeout=10)
    assert run.status == "run"
    assert run.exit_code == 0
    assert run.stdout.strip() == "hi"
    assert run.duration_ms >= 0


def test_run_tool_nonzero_exit_is_still_a_run(tmp_path):
    run = runner.run_tool(["python3", "-c", "raise SystemExit(3)"], cwd=str(tmp_path),
                          timeout=10)
    assert run.status == "run" and run.exit_code == 3


def test_run_tool_timeout_is_reported_not_raised(tmp_path, monkeypatch):
    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="slow", timeout=1)

    monkeypatch.setattr(runner.subprocess, "run", _timeout)
    run = runner.run_tool(["slow"], cwd=str(tmp_path), timeout=1)
    assert run.status == "timeout" and run.exit_code is None
    assert "timed out after 1s" in run.error


def test_run_tool_missing_binary_is_reported_not_raised(tmp_path):
    run = runner.run_tool(["definitely-not-a-real-binary-xyz"], cwd=str(tmp_path),
                          timeout=5)
    assert run.status == "failed" and run.error


def test_run_tool_never_uses_a_shell_and_pins_cwd(tmp_path, monkeypatch):
    seen = {}
    real = subprocess.run

    def spy(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        return real(["python3", "-c", "print('ok')"], **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", spy)
    runner.run_tool(["tool", "-x"], cwd=str(tmp_path), timeout=5, env={"EXTRA": "1"})
    assert seen["argv"] == ["tool", "-x"]
    assert "shell" not in seen["kwargs"]
    assert seen["kwargs"]["cwd"] == str(tmp_path)
    assert seen["kwargs"]["timeout"] == 5
    assert seen["kwargs"]["env"]["EXTRA"] == "1"
    assert seen["kwargs"]["env"]["NO_COLOR"] == "1"


def test_load_json_payload_handles_array_object_and_ndjson():
    assert runner.load_json_payload('[{"a": 1}]') == [{"a": 1}]
    assert runner.load_json_payload('{"a": 1}') == [{"a": 1}]
    assert runner.load_json_payload('{"a": 1}\n{"b": 2}\n') == [{"a": 1}, {"b": 2}]
    assert runner.load_json_payload("   ") == []


def test_load_json_payload_rejects_garbage():
    with pytest.raises(ValueError):
        runner.load_json_payload("not json at all")
