"""Secret redaction (pre-provider) and offline staged-content secret scan."""
from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path

from ai_review.models import Finding, StagedChange

SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("private-key", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
        r".*?"
        r"-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----",
        re.S,
    )),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b")),
    ("password", re.compile(
        r"(?i)(?<!:)\b(password|passwd|pwd|secret|api[_-]?key|token)\b\s*[=:]"
        r"\s*['\"]?[A-Za-z0-9_@#$%^&+=!.\-/]{8,}"
    )),
    ("connstr", re.compile(r"(?i)\b(?:postgres|postgresql|mysql|mongodb|redis|amqp)://[^\s'\"$]+")),
]


def _placeholder(kind: str, match: str) -> str:
    digest = hashlib.sha256(match.encode("utf-8")).hexdigest()[:10]
    return f"[REDACTED:{kind}:{digest}]"


def redact_text(
    text: str,
    patterns: list[tuple[str, re.Pattern]] | None = None,
) -> tuple[str, list[str]]:
    """Replace secret-looking substrings with deterministic placeholders.

    Matches every pattern in order, substituting in place so a later pattern
    sees the already-redacted text with no raw secret surviving. Returns the
    redacted text and the list of matched secret kinds (deduplicated, in first
    match order). ``patterns=None`` uses :data:`SECRET_PATTERNS`; an explicit
    empty list means *no* redaction.
    """
    patterns = SECRET_PATTERNS if patterns is None else patterns
    redacted = text
    kinds: list[str] = []
    for kind, pattern in patterns:
        def repl(m: re.Match, _k: str = kind) -> str:
            if _k not in kinds:
                kinds.append(_k)
            return _placeholder(_k, m.group(0))
        redacted = pattern.sub(repl, redacted)
    return redacted, kinds


def should_exclude(path: str, excluded: list[str]) -> bool:
    """Return True when *path* matches any exclusion pattern.

    Matches on the full path, the bare basename (e.g. ``*.pem`` flags
    ``secrets/key.pem``), and a trailing-slash directory form (``dir/`` flags
    everything under ``dir``).
    """
    base = Path(path).name
    return any(
        fnmatch.fnmatch(path, p)
        or fnmatch.fnmatch(base, p)
        or fnmatch.fnmatch(path, p.rstrip("/") + "/*")
        for p in excluded
    )


def scan_staged(
    repo_dir: str,
    changes: list[StagedChange],
    excluded: list[str] | None = None,
) -> list[Finding]:
    """Scan staged file contents for hardcoded secrets.

    Added, modified and renamed changes are read from the repo working tree;
    deleted and binary changes are skipped, as are files matching *excluded*
    (default: env files and common key formats). ``excluded=None`` uses the
    default list; an explicit empty list excludes nothing. Every match yields
    a CRITICAL ``SECURITY`` finding whose evidence is the deterministic
    placeholder (never the raw secret) with ``confidence=1.0`` and
    ``hard_block=True`` so the policy engine can block without an LLM call.

    Note: the diff contract is the staged blob, so this reads the on-disk file
    at ``repo_dir``; the pipeline wires redaction before any provider call.
    """
    excluded = [".env", "*.pem", "*.key", "*.p12"] if excluded is None else excluded
    findings: list[Finding] = []
    for change in changes:
        if change.status == "deleted" or change.is_binary:
            continue
        if should_exclude(change.path, excluded):
            continue
        path = Path(repo_dir) / change.path
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for kind, pattern in SECRET_PATTERNS:
            for m in re.finditer(pattern, content):
                findings.append(Finding(
                    severity="CRITICAL",
                    category="SECURITY",
                    file=change.path,
                    line=content.count("\n", 0, m.start()) + 1,
                    title=f"Possible {kind} secret in staged change",
                    description=f"Staged content looks like a {kind} credential.",
                    evidence=_placeholder(kind, m.group(0)),
                    recommendation="Remove the secret; use environment variables or a secret manager.",
                    confidence=1.0,
                    hard_block=True,
                ))
                break
    return findings
