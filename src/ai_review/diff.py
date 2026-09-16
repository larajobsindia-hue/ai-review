"""Minimal unified-diff parser sufficient for hunk-aware review."""
from __future__ import annotations

import re

from ai_review.models import Hunk, StagedChange

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_PATH_RE = re.compile(r"^diff --git a/(.*?) b/(.*)$")


def _strip_prefix(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


def parse_unified_diff(text: str) -> dict[str, list[Hunk]]:
    files: dict[str, list[Hunk]] = {}
    current: str | None = None
    new_line = 0
    hunk: Hunk | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("diff --git"):
            match = _PATH_RE.match(line)
            current = _strip_prefix(match.group(2)) if match else None
            hunk = None
            new_line = 0
            if current is not None:
                files.setdefault(current, [])
            continue
        if current is None or line.startswith(("--- ", "+++ ", "index ")):
            continue
        header = _HUNK_RE.match(line)
        if header:
            old_start = int(header.group(1))
            old_count = int(header.group(2) or 1)
            new_start = int(header.group(3))
            new_count = int(header.group(4) or 1)
            hunk = Hunk(old_start, old_count, new_start, new_count)
            files[current].append(hunk)
            new_line = new_start
            continue
        if hunk is None:
            continue
        body = line[1:]
        if line.startswith("+"):
            hunk.added_lines.append(body)
            hunk.changed_new_lines.add(new_line)
            new_line += 1
        elif line.startswith("-"):
            hunk.removed_lines.append(body)
        elif line.startswith(" "):
            new_line += 1
    return files


def attach_hunks(changes: list[StagedChange], parsed: dict[str, list[Hunk]]) -> None:
    for change in changes:
        change.hunks = parsed.get(change.path, [])


def changed_new_ranges(change: StagedChange) -> set[int]:
    out: set[int] = set()
    for hunk in change.hunks:
        out.update(hunk.changed_new_lines)
    return out
