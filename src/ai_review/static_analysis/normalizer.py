"""Tool output -> normalized ``Finding`` (spec §18/§19/§20/§21).

Severity and confidence are separate axes, each tool keeps its own explicit
mapping (never a universal ``ERROR -> CRITICAL``), and the analyzer's original
severity/message survive normalization.
"""
from __future__ import annotations

import hashlib
import os
import re

from ai_review.models import Category, Finding, Severity

#: Severity ranking used for merging and ordering (higher wins).
SEVERITY_ORDER: dict[str, int] = {
    "CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0,
}

#: Confidence vocabulary -> numeric (spec §21).
CONFIDENCE_MAP: dict[str, float] = {"high": 0.9, "medium": 0.6, "low": 0.4}

DEFAULT_CONFIDENCE = 0.6

_WHITESPACE = re.compile(r"\s+")

#: First-match-wins keyword table mapping a tool's rule id/category onto the
#: project's existing Category enum. Order matters: security first.
CATEGORY_HINTS: tuple[tuple[tuple[str, ...], Category], ...] = (
    (("security", "inject", "xss", "csrf", "sqli", "secret", "crypto",
      "traversal", "deseriali", "eval"), "SECURITY"),
    (("performance", "perf", "slow", "optimiz", "complexity"), "PERFORMANCE"),
    (("concurrency", "race", "deadlock", "thread", "async"), "CONCURRENCY"),
    (("null", "nil", "undefined", "type", "correctness", "bug", "error-prone",
      "impossible", "nonobject", "notfound"), "BUG"),
    (("efficiency", "resource", "leak", "unclosed", "close"), "RESOURCE"),
    (("error-handl", "exception", "handling", "unchecked"), "ERROR_HANDLING"),
    (("api", "compat", "deprecat", "breaking"), "API_COMPAT"),
    (("test", "coverage"), "TESTING"),
    (("config", "env", "yaml", "docker"), "CONFIG"),
    (("style", "maintainab", "readab", "convention", "naming", "indent",
      "deadcode", "dead-code", "unused", "documentation", "missingtype"),
     "MAINTAINABILITY"),
)


def normalize_severity(raw: str | None, mapping: dict[str, Severity],
                       default: Severity = "MEDIUM") -> Severity:
    """Map a tool's own severity string through an explicit per-tool table."""
    return mapping.get((raw or "").strip().upper(), default)


def confidence_from(raw: str | None, default: float = DEFAULT_CONFIDENCE) -> float:
    """Map a tool's confidence vocabulary (or nothing) to the 0..1 float."""
    return CONFIDENCE_MAP.get((raw or "").strip().lower(), default)


def infer_category(*hints: str | None, default: Category = "OTHER") -> Category:
    """Deterministic keyword inference when a tool exposes no category."""
    blob = " ".join(hint for hint in hints if hint).lower()
    if not blob:
        return default
    for needles, category in CATEGORY_HINTS:
        if any(needle in blob for needle in needles):
            return category
    return default


def normalize_message(text: str) -> str:
    """Lowercased, whitespace-collapsed message used for dedup comparisons."""
    return _WHITESPACE.sub(" ", (text or "").strip()).lower()


def relative_path(path: str | None, repo_root: str) -> str:
    """Repo-relative path for a tool's absolute output.

    Tools disagree: some echo the relative target they were handed, others
    resolve it against their own config. The pipeline's staged paths are always
    repo-relative, so an absolute tool path is rebased here; a path that cannot
    be rebased (another drive/mount on Windows) is kept as reported rather than
    mangled.
    """
    text = str(path or "")
    if text and os.path.isabs(text):
        try:
            return os.path.relpath(text, repo_root)
        except ValueError:                # different drive/mount on Windows
            return text
    return text


def stable_id(file: str, line: int | None, category: str, message: str) -> str:
    """Tool-independent 12-hex-char id for one located defect.

    Two tools describing the same file/line/category/message therefore share an
    id, which is what makes "Detected by semgrep, phpstan" renderable and makes
    AI ``related_static_finding_id`` links stable across runs.
    """
    key = "\x1f".join([file or "", str(line or 0), category or "",
                       normalize_message(message)])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def worst_severity(left: Severity, right: Severity) -> Severity:
    """The higher-ranked of two severities (used when merging duplicates)."""
    return left if SEVERITY_ORDER.get(left, 0) >= SEVERITY_ORDER.get(right, 0) else right


def make_finding(*, tool: str, severity: Severity, file: str, message: str,
                 rule_id: str | None = None, fingerprint: str | None = None,
                 original_severity: str | None = None, category: Category | None = None,
                 line: int | None = None, confidence: float | None = None,
                 evidence: str = "", recommendation: str = "",
                 detected_by: list[str] | None = None) -> Finding:
    """Build one normalized static finding.

    The analyzer's severity and message are preserved verbatim in
    ``original_severity`` / ``original_message`` (spec §19) while the normalized
    severity/category/confidence drive merging, ordering and rendering.
    ``hard_block`` stays False: tools are evidence, never a gate (spec §35).
    """
    resolved_category = category or infer_category(rule_id, tool)
    title = (message or rule_id or f"{tool} finding").strip().splitlines()[0][:200]
    return Finding(
        severity=severity,
        category=resolved_category,
        file=file,
        line=line,
        title=title,
        description=(message or "").strip(),
        evidence=evidence or (f"rule: {rule_id}" if rule_id else ""),
        recommendation=recommendation or (
            "Review the reported finding; fix it, or suppress it with a documented reason."
        ),
        confidence=DEFAULT_CONFIDENCE if confidence is None else confidence,
        source="static_analysis",
        id=stable_id(file, line, resolved_category, message),
        tool=tool,
        rule_id=rule_id or None,
        fingerprint=fingerprint,
        original_severity=original_severity,
        original_message=(message or "").strip(),
        detected_by=list(detected_by or [tool]),
    )
