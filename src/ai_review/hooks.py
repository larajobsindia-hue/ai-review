"""Pre-commit hook install / uninstall with safe integration."""
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
    head = "#!/usr/bin/env bash\n"
    marker_line = f"# {HOOK_MARKER} v{__version__} :: do-not-edit\n"
    backup = (
        BACKUP_SEGMENT.format(marker=HOOK_MARKER, backup=BACKUP_NAME)
        if has_backup
        else ""
    )
    run = RUN_SEGMENT.format(exe=exe)
    return head + marker_line + backup + run


def uninstall_hook(repo_dir: str) -> str:
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
