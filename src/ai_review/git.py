"""Safe subprocess wrapper for git, staging-aware collection."""
from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

from ai_review.models import FileStatus, Hunk, StagedChange


class GitError(RuntimeError):
    pass


def _run_git(cwd: str, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0] if args else '?'} timed out") from exc


def _check(proc: subprocess.CompletedProcess, label: str) -> None:
    if proc.returncode != 0:
        raise GitError(f"git {label} failed: {proc.stderr.strip()[:500]}")


def _ignored(path: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    base = os.path.basename(path)
    return any(
        fnmatch.fnmatch(path, p) or fnmatch.fnmatch(base, p) or p.rstrip("/") in (path, base)
        for p in patterns
    )


def git_repo_root(cwd: str) -> str:
    proc = _run_git(cwd, ["rev-parse", "--show-toplevel"])
    _check(proc, "rev-parse")
    return proc.stdout.strip()


def git_branch(cwd: str) -> str:
    proc = _run_git(cwd, ["symbolic-ref", "--short", "-q", "HEAD"])
    if proc.returncode != 0:
        proc = _run_git(cwd, ["rev-parse", "--short", "HEAD"])
    return proc.stdout.strip() or "detached"


def git_diff_text(cwd: str, ignore: tuple[str, ...] = ()) -> str:
    proc = _run_git(cwd, ["diff", "--cached", "--no-ext-diff"])
    _check(proc, "diff --cached")
    return proc.stdout


def git_name_status(cwd: str, ignore: tuple[str, ...] = ()) -> list[tuple[str, list[str]]]:
    proc = _run_git(cwd, ["diff", "--cached", "--name-status", "-z", "-M"])
    _check(proc, "name-status")
    raw = proc.stdout.split("\0")
    pairs: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(raw):
        entry = raw[i]
        if not entry:
            i += 1
            continue
        if entry[0] in ("A", "M", "D"):
            pairs.append((entry, [raw[i + 1]] if i + 1 < len(raw) else []))
            i += 2
        elif entry.startswith("R"):
            pairs.append((entry, raw[i + 1 : i + 3] if i + 2 < len(raw) else []))
            i += 3
        else:
            i += 1
    return pairs


def git_numstat(cwd: str, ignore: tuple[str, ...] = ()) -> dict[str, tuple[int, int, bool]]:
    proc = _run_git(cwd, ["diff", "--cached", "--numstat", "-z"])
    _check(proc, "numstat")
    out: dict[str, tuple[int, int, bool]] = {}
    for field in proc.stdout.split("\0"):
        if not field:
            continue
        parts = field.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        if added == "-" or removed == "-":
            out[path] = (0, 0, True)
        else:
            out[path] = (int(added), int(removed), False)
    return out


def collect_staged(cwd: str, ignore: tuple[str, ...] = ()) -> list[StagedChange]:
    root = git_repo_root(cwd)
    name_status = git_name_status(root, ignore)
    numstat = git_numstat(root, ignore)
    changes: list[StagedChange] = []
    for status, paths in name_status:
        if status.startswith("R"):
            old, new = paths
            if _ignored(new, ignore):
                continue
            added, removed, binary = numstat.get(new, (0, 0, False))
            changes.append(StagedChange(path=new, status="renamed", old_path=old,
                                        is_binary=binary, stat_added=added, stat_removed=removed))
        else:
            path = paths[0]
            if _ignored(path, ignore):
                continue
            kind: FileStatus = {"A": "added", "M": "modified", "D": "deleted"}[status]
            added, removed, binary = numstat.get(path, (0, 0, False))
            changes.append(StagedChange(path=path, status=kind, is_binary=binary,
                                        stat_added=added, stat_removed=removed))
    return changes
