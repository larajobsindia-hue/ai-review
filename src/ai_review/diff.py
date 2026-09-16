"""Minimal unified-diff parser sufficient for hunk-aware review."""
from __future__ import annotations

import re

from ai_review.models import Hunk, StagedChange

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_DIFF_GIT_PREFIX = "diff --git "

_C_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def _strip_prefix(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


def _unquote_git_path(path: str) -> str:
    """Undo git's C-style quoting (core.quotePath): "..." with \\ \\" and \\NNN."""
    if len(path) < 2 or path[0] != '"' or path[-1] != '"':
        return path
    body = path[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in _C_ESCAPES:
                out += _C_ESCAPES[nxt].encode("utf-8")
                i += 2
                continue
            digits = ""
            j = i + 1
            while j < len(body) and len(digits) < 3 and body[j] in "01234567":
                digits += body[j]
                j += 1
            if digits:
                out.append(int(digits, 8))
                i = j
                continue
            out += nxt.encode("utf-8")
            i += 2
            continue
        out += ch.encode("utf-8")
        i += 1
    return out.decode("utf-8", errors="replace")


def _split_header_paths(rest: str) -> tuple[str, str] | None:
    """Split the tail of a `diff --git` line into its two path tokens.

    Tokens are either bare (space-delimited) or double-quoted; quoted tokens
    may contain spaces and backslash escapes.
    """
    paths: list[str] = []
    i = 0
    n = len(rest)
    while len(paths) < 2:
        while i < n and rest[i] == " ":
            i += 1
        if i >= n:
            return None
        if rest[i] == '"':
            i += 1
            buf: list[str] = []
            closed = False
            while i < n:
                ch = rest[i]
                if ch == "\\" and i + 1 < n:
                    buf.append(rest[i : i + 2])
                    i += 2
                    continue
                if ch == '"':
                    i += 1
                    closed = True
                    break
                buf.append(ch)
                i += 1
            if not closed:
                return None
            paths.append('"' + "".join(buf) + '"')
        else:
            j = rest.find(" ", i)
            if j == -1:
                paths.append(rest[i:])
                i = n
            else:
                paths.append(rest[i:j])
                i = j
    return paths[0], paths[1]


def _header_new_path(line: str) -> str | None:
    """Extract the b/ path; unquoted paths may contain spaces, so when no
    token is quoted (a path needing quoting would contain '"'), take
    everything after the LAST " b/" separator, matching git's own
    disambiguation. Quoted/mixed headers go through the tokenizer."""
    rest = line[len(_DIFF_GIT_PREFIX):]
    if '"' not in rest:
        new = rest.rsplit(" b/", 1)[-1]
        return _strip_prefix(new) if new else None
    parts = _split_header_paths(rest)
    if parts is None:
        return None
    _, new = parts
    return _strip_prefix(_unquote_git_path(new))


def parse_unified_diff(text: str) -> dict[str, list[Hunk]]:
    files: dict[str, list[Hunk]] = {}
    current: str | None = None
    new_line = 0
    hunk: Hunk | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("diff --git"):
            current = _header_new_path(line)
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
