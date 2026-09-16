"""Unit tests for hooks.py: install / uninstall / safe wrapper behavior."""
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from ai_review.hooks import (
    BACKUP_NAME,
    HOOK_MARKER,
    _render_wrapper,
    install_hook,
    uninstall_hook,
)

BASH = shutil.which("bash")
SH = shutil.which("sh")


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "T")
    return tmp_path


def test_install_creates_hook(repo):
    message = install_hook(str(repo), exe="ai-review")
    hook = repo / ".git" / "hooks" / "pre-commit"
    assert message == "installed"
    assert hook.is_file()
    assert HOOK_MARKER in hook.read_text()
    assert "ai-review" in hook.read_text()


def test_install_creates_missing_hooks_dir(repo):
    shutil.rmtree(repo / ".git" / "hooks")
    message = install_hook(str(repo), exe="ai-review")
    assert message == "installed"
    assert (repo / ".git" / "hooks" / "pre-commit").is_file()


def test_install_backs_up_existing_hook(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'existing'\n")
    hook.chmod(0o755)
    message = install_hook(str(repo), exe="ai-review")
    assert message == "installed (wrapped existing hook)"
    backup = repo / ".git" / "hooks" / "pre-commit.ai-review-backup"
    assert backup.read_text().startswith("#!/bin/sh")


def test_reinstall_over_own_hook_preserves_original_backup(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    original = "#!/bin/sh\necho 'precious'\n"
    hook.write_text(original)
    hook.chmod(0o755)
    install_hook(str(repo), exe="ai-review")
    backup = repo / ".git" / "hooks" / BACKUP_NAME
    first = backup.read_text()
    install_hook(str(repo), exe="ai-review")
    assert backup.read_text() == first
    assert backup.read_text() == original


def test_uninstall_restores_backup(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'existing'\n")
    hook.chmod(0o755)
    install_hook(str(repo), exe="ai-review")
    message = uninstall_hook(str(repo))
    assert message == "restored"
    assert hook.read_text().startswith("#!/bin/sh")
    assert HOOK_MARKER not in hook.read_text()
    assert not (repo / ".git" / "hooks" / BACKUP_NAME).exists()


def test_uninstall_removes_own_hook(repo):
    install_hook(str(repo), exe="ai-review")
    message = uninstall_hook(str(repo))
    assert message == "removed"
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()


def test_uninstall_foreign_hook_is_no_op(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'not mine'\n")
    hook.chmod(0o755)
    message = uninstall_hook(str(repo))
    assert message == "no op"
    assert hook.read_text() == "#!/bin/sh\necho 'not mine'\n"


def test_uninstall_missing_hook_is_no_op(repo):
    assert uninstall_hook(str(repo)) == "no op"


def test_uninstall_unreadable_hook_is_no_op(repo):
    # A directory at the hook path makes read_text() raise OSError.
    (repo / ".git" / "hooks" / "pre-commit").mkdir()
    assert uninstall_hook(str(repo)) == "no op"


def test_backup_chmod_preserved(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ntrue\n")
    hook.chmod(0o755)
    install_hook(str(repo), exe="ai-review")
    backup = repo / ".git" / "hooks" / BACKUP_NAME
    assert stat.S_IMODE(backup.stat().st_mode) == 0o755


def test_installed_hook_is_executable(repo):
    install_hook(str(repo), exe="ai-review")
    hook = repo / ".git" / "hooks" / "pre-commit"
    assert stat.S_IMODE(hook.stat().st_mode) == 0o755


@pytest.mark.skipif(SH is None, reason="sh not available")
@pytest.mark.parametrize("has_backup", [True, False])
def test_wrapper_sh_syntax(has_backup):
    script = _write_and_chmod(_render_wrapper("ai-review", has_backup=has_backup))
    assert subprocess.run([SH, "-n", str(script)]).returncode == 0


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("has_backup", [True, False])
def test_wrapper_bash_syntax(has_backup):
    script = _write_and_chmod(_render_wrapper("ai-review", has_backup=has_backup))
    assert subprocess.run([BASH, "-n", str(script)]).returncode == 0


def test_wrapper_shebang_is_posix_sh():
    assert _render_wrapper("ai-review", has_backup=False).startswith("#!/bin/sh\n")


def test_wrapper_honors_ai_review_exe_env_override():
    text = _render_wrapper("ai-review", has_backup=False)
    assert "${AI_REVIEW_EXE:-ai-review}" in text
    assert 'command -v "$_EXE"' in text


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_wrapper_runs_backup_then_ours(tmp_path):
    hook_dir = tmp_path / "hooks"
    hook_dir.mkdir()
    foreign = hook_dir / "pre-commit.ai-review-backup"
    foreign.write_text("#!/bin/sh\necho 'existing-ran'\n")
    foreign.chmod(0o755)
    text = _render_wrapper("ai-review", has_backup=True)
    script = _write_and_chmod(text, hook_dir / "pre-commit")
    env = dict(os.environ, AI_REVIEW_EXE="/bin/true")
    proc = subprocess.run([SH, str(script)], capture_output=True, text=True, env=env)
    assert proc.returncode == 0
    assert "existing-ran" in proc.stdout


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_wrapper_propagates_failure_exit_code(tmp_path):
    hook_dir = tmp_path / "hooks"
    hook_dir.mkdir()
    text = _render_wrapper("ai-review", has_backup=False)
    script = _write_and_chmod(text, hook_dir / "pre-commit")
    failing = hook_dir / "failer"
    failing.write_text("#!/bin/sh\nexit 3\n")
    failing.chmod(0o755)
    env = dict(os.environ, AI_REVIEW_EXE=str(failing))
    proc = subprocess.run([SH, str(script)], capture_output=True, text=True, env=env)
    assert proc.returncode == 3


@pytest.mark.skipif(SH is None, reason="sh not available")
def test_wrapper_fails_open_when_exe_missing(tmp_path):
    # Deliberate semantic: with the ai-review executable absent, the hook
    # FAILS OPEN (exit 0) after warning on stderr — a missing install must
    # never block every commit in the repo.
    script = _write_and_chmod(_render_wrapper("ai-review", has_backup=False))
    env = dict(os.environ, AI_REVIEW_EXE="nonexistent-xyz-abc")
    proc = subprocess.run([SH, str(script)], capture_output=True, text=True, env=env)
    assert proc.returncode == 0
    assert "executable not found" in proc.stderr
    assert "nonexistent-xyz-abc" in proc.stderr


def _write_and_chmod(text, path=None):
    path = Path(path or Path(tempfile.mkdtemp(prefix="ai-review-hook-")) / "pre-commit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path
