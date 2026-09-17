"""Safe subprocess execution for analyzers: argv lists only, never a shell.

One place builds processes, so no analyzer can introduce a shell string, an
unpinned cwd, an interactive stdin, or an unbounded wait (spec §16).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass

#: Hard cap on retained stdout (a pathological tool must not exhaust memory).
MAX_STDOUT_CHARS = 8_000_000
#: Only this much stderr is retained (it is reported, never parsed).
STDERR_TAIL_CHARS = 4_000


@dataclass
class ToolRun:
    """Raw outcome of one tool invocation."""
    argv: list[str]
    status: str = "run"              # run | timeout | failed
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    duration_ms: int = 0


def run_tool(argv: list[str], *, cwd: str, timeout: float,
             env: dict[str, str] | None = None) -> ToolRun:
    """Run *argv* with captured output; never raises for tool behaviour.

    Contract: ``argv`` is a list (``shell=True`` is forbidden project-wide),
    cwd is pinned to the repository root, stdin is closed so a tool cannot
    block on a prompt, output is decoded with ``errors="replace"`` (tools emit
    non-UTF-8 bytes), and the timeout yields ``status="timeout"`` instead of an
    exception. Only the caller's own bugs propagate.
    """
    merged = dict(os.environ)
    merged.setdefault("NO_COLOR", "1")        # keep ANSI codes out of JSON output
    if env:
        merged.update(env)
    started = time.monotonic()

    def _elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        proc = subprocess.run(
            list(argv), cwd=cwd, env=merged, capture_output=True, text=True,
            errors="replace", timeout=timeout, check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return ToolRun(argv=list(argv), status="timeout",
                       error=f"timed out after {timeout:g}s", duration_ms=_elapsed())
    except OSError as exc:                     # missing/inaccessible binary
        return ToolRun(argv=list(argv), status="failed", error=str(exc),
                       duration_ms=_elapsed())
    return ToolRun(
        argv=list(argv), status="run", exit_code=proc.returncode,
        stdout=proc.stdout[:MAX_STDOUT_CHARS], stderr=proc.stderr[-STDERR_TAIL_CHARS:],
        duration_ms=_elapsed(),
    )


def load_json_payload(text: str) -> list[dict]:
    """Decode a tool's JSON output: array, single object, or NDJSON.

    Tools disagree: eslint/ruff/semgrep emit one array, ``staticcheck -f json``
    has emitted both an array and one-object-per-line. Empty output is ``[]``
    (a clean run); anything undecodable raises ``ValueError`` so the analyzer's
    parser can report a ``failed`` tool instead of inventing findings.
    """
    stripped = text.strip()
    if not stripped:
        return []
    data = None
    try:
        data = json.loads(stripped)
    except ValueError:
        data = None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    out: list[dict] = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    if not out:
        raise ValueError("no JSON payload found in tool output")
    return out
