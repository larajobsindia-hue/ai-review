"""Pre-commit hook install / uninstall with safe integration.

Layout limitation (Phase 1 semantic): :func:`_hooks_dir` assumes a standard
``<repo>/.git/hooks`` directory. Repos using ``core.hooksPath``, git
worktrees/submodules (where ``.git`` is a file) or a relocated hooks dir are
not supported — install fails loud with an error (CLI exit 2) and never
corrupts an existing hook.
"""
from __future__ import annotations

from pathlib import Path

from ai_review import __version__

HOOK_MARKER = "ai-review hook:"
BACKUP_NAME = "pre-commit.ai-review-backup"

BACKUP_SEGMENT = """_BACKUP="$(dirname "$0")/{backup}"
if [ -x "$_BACKUP" ] && ! grep -q '^{marker}' "$_BACKUP" 2>/dev/null; then
  "$_BACKUP" || exit $?
fi
"""

RUN_SEGMENT = """_EXE="${{AI_REVIEW_EXE:-{exe}}}"
if command -v "$_EXE" >/dev/null 2>&1; then
  "$_EXE" --staged || exit $?
else
  echo "ai-review: executable not found on PATH: $_EXE" >&2
fi
"""


def _hooks_dir(repo_dir: str) -> Path:
    return Path(repo_dir) / ".git" / "hooks"


def _is_ours(path: Path) -> bool:
    try:
        return HOOK_MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def install_hook(repo_dir: str, exe: str = "ai-review") -> str:
    """Install our wrapper as ``<repo>/.git/hooks/pre-commit``.

    Returns ``"installed"`` (no prior hook) or ``"installed (wrapped existing
    hook)"`` (a foreign hook was displaced). Re-installing over our own hook
    rewrites the wrapper and leaves the existing backup untouched.

    The backup always mirrors the *currently displaced* hook: installing over
    foreign hook A, then over foreign hook B, replaces the backup with B
    (single-generation backup; A is gone — deliberate Phase 1 semantic).
    """
    hooks = _hooks_dir(repo_dir)
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    backup = hooks / BACKUP_NAME
    if hook.exists() and not _is_ours(hook):
        backup.write_text(hook.read_text(encoding="utf-8", errors="replace"))
        backup.chmod(hook.stat().st_mode)
        hook.chmod(0o644)
    content = _render_wrapper(exe, backup.exists())
    hook.write_text(content, encoding="utf-8")
    hook.chmod(0o755)
    if backup.exists():
        return "installed (wrapped existing hook)"
    return "installed"


def _render_wrapper(exe: str, has_backup: bool) -> str:
    # POSIX sh proven (dash): the wrapper uses only sh-legal constructs.
    head = "#!/bin/sh\n"
    marker_line = f"# {HOOK_MARKER} v{__version__} :: do-not-edit\n"
    backup = (
        BACKUP_SEGMENT.format(marker=HOOK_MARKER, backup=BACKUP_NAME)
        if has_backup
        else ""
    )
    run = RUN_SEGMENT.format(exe=exe)
    return head + marker_line + backup + run


def uninstall_hook(repo_dir: str) -> str:
    """Remove only our hook integration.

    Returns ``"restored"`` (backup written back over our wrapper, backup
    deleted), ``"removed"`` (our hook deleted, nothing to restore), or
    ``"no op"`` (no hook, or a foreign hook we must not touch).
    """
    hooks = _hooks_dir(repo_dir)
    hook = hooks / "pre-commit"
    backup = hooks / BACKUP_NAME
    if not hook.exists() or not _is_ours(hook):
        return "no op"
    if backup.exists():
        hook.write_text(backup.read_text(encoding="utf-8", errors="replace"))
        hook.chmod(backup.stat().st_mode)
        backup.unlink()
        return "restored"
    hook.unlink()
    return "removed"
