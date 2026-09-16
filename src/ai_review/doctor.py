"""Diagnostics: env, repo, config, detection, prompts, redaction, hook, LLM.

``run_doctor`` probes the environment in a fixed order and returns
``(exit_code, lines)`` where each line is ``✓/⚠/✗ label — note``. Exit code is
0 when there are no errors (warnings allowed), 1 otherwise. A missing or
unreachable local LLM server degrades to a warning, never a hard failure —
doctor must succeed offline.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import httpx

from ai_review.config import load_config_for_repo
from ai_review.detector import detect
from ai_review.git import GitError, git_repo_root
from ai_review.hooks import _is_ours
from ai_review.prompts import default_prompt_dir, prompt_files_ok
from ai_review.security import redact_text

#: Sample secret for the redaction self-test (the canonical AWS example key).
_SECRET_SAMPLE = "key=AKIAIOSFODNN7EXAMPLE"


def run_doctor(repo_dir: str) -> tuple[int, list[str]]:
    """Probe the installation and return ``(exit_code, report_lines)``."""
    lines = ["AI Review Doctor", ""]
    errors = 0
    warns = 0

    def ok(label: str) -> None:
        lines.append(f"✓ {label}")

    def warn(label: str, note: str = "") -> None:
        nonlocal warns
        warns += 1
        lines.append(f"⚠ {label}" + (f" — {note}" if note else ""))

    def err(label: str, note: str = "") -> None:
        nonlocal errors
        errors += 1
        lines.append(f"✗ {label}" + (f" — {note}" if note else ""))

    if shutil.which("git"):
        ok("Git binary")
    else:
        err("Git binary", "git not found on PATH")

    try:
        root = git_repo_root(repo_dir)
        ok("Git repository")
    except GitError as exc:
        err("Git repository", str(exc))
        root = repo_dir

    try:
        cfg = load_config_for_repo(repo_dir)
        ok("Configuration")
    except Exception as exc:
        err("Configuration", str(exc))
        cfg = None

    try:
        profile = detect([], root)
        if profile.languages or profile.frameworks:
            names = ", ".join(profile.all_names()[:8])
            ok(f"Technology detection ({names})")
        else:
            warn("Technology detection", "no known technology detected")
    except Exception as exc:
        err("Technology detection", str(exc))

    prompt_ok, missing = prompt_files_ok(default_prompt_dir())
    if prompt_ok:
        ok("Prompt files")
    else:
        err("Prompt files", "missing: " + ", ".join(missing))

    redacted, kinds = redact_text(_SECRET_SAMPLE)
    if _SECRET_SAMPLE in redacted or not kinds:
        err("Secret redaction", "self-test failed")
    else:
        ok("Secret redaction")

    hook = Path(root) / ".git" / "hooks" / "pre-commit"
    if hook.exists() and _is_ours(hook):
        ok("Git hook (installed by ai-review)")
    elif hook.exists():
        warn("Git hook", "existing hook present (not ours)")
    else:
        warn("Git hook", "not installed")

    if cfg is not None:
        status, note = _probe_llm(cfg)
        if status == "ok":
            ok("LLM endpoint")
        else:
            warn("LLM endpoint", note)
    else:
        err("LLM endpoint", "skipped (no config)")

    lines.append("")
    if errors:
        lines.append(f"System not ready: {errors} error(s), {warns} warning(s).")
        return 1, lines
    if warns:
        lines.append(f"System ready (with {warns} warning(s)).")
    else:
        lines.append("System ready.")
    return 0, lines


def _probe_llm(cfg) -> tuple[str, str]:
    """Probe ``GET {endpoint}/v1/models`` within 3s; returns ``(status, note)``.

    Any connection failure is a *warning*, not an error: a missing local LLM
    server must not make doctor report the system as broken.
    """
    url = cfg.llm.endpoint.rstrip("/") + "/v1/models"
    try:
        response = httpx.get(url, timeout=3.0)
    except Exception:
        return "warn", f"{cfg.llm.provider} server unreachable"
    if response.status_code == 200:
        return "ok", ""
    return "warn", f"HTTP {response.status_code}"
