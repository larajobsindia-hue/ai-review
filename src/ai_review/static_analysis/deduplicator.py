"""Deterministic deduplication of static findings (spec §22).

Order-independence is a contract: the same change set must always produce the
same findings in the same order, whatever order the tools finished in.
"""
from __future__ import annotations

from ai_review.models import Finding
from ai_review.static_analysis.normalizer import (SEVERITY_ORDER, normalize_message,
                                                  stable_id, worst_severity)


def _sort_key(finding: Finding):
    return (finding.file, finding.line or 0, -SEVERITY_ORDER.get(finding.severity, 0),
            finding.tool or "", finding.rule_id or "",
            normalize_message(finding.original_message or finding.title))


def _exact_key(finding: Finding):
    """Same tool reporting the *same problem* at the same location.

    ``Finding.id`` already identifies a located problem independently of which
    tool reported it, so ``(tool, id)`` is exact. Hand-built findings without an
    id fall back to their location + rule + category + message: a bare
    ``(tool, rule, file, line)`` key would wrongly collapse two different
    problems that a tool reports at the same line with no rule id (eslint fatal
    parse errors, phpstan file-level errors).
    """
    if finding.id:
        return (finding.tool or "", finding.id)
    return (finding.tool or "", finding.rule_id or "", finding.file, finding.line or 0,
            finding.category, normalize_message(finding.original_message or finding.title))


def _same_issue(left: Finding, right: Finding) -> bool:
    """True only for a genuine cross-tool duplicate (never merely nearby).

    Two tools at the same file:line with the same normalized wording are the
    same defect, even when they categorize it differently (semgrep calls it
    SECURITY, phpstan calls it BUG). When the wording differs, the match needs
    both the category *and* a shared rule id/fingerprint, so findings that are
    merely near each other are never merged (spec §22).
    """
    if left.file != right.file or (left.line or 0) != (right.line or 0):
        return False
    if normalize_message(left.original_message or left.title) == \
            normalize_message(right.original_message or right.title):
        return True
    if left.category != right.category:
        return False
    if left.rule_id and left.rule_id == right.rule_id:
        return True
    return bool(left.fingerprint and left.fingerprint == right.fingerprint)


def _merge_into(target: Finding, other: Finding) -> None:
    """Fold *other* into *target*: worst severity, unioned provenance."""
    if worst_severity(target.severity, other.severity) != target.severity:
        target.severity = other.severity
        target.original_severity = other.original_severity or target.original_severity
        target.title = other.title
        target.description = other.description
    for tool in other.detected_by or ([other.tool] if other.tool else []):
        if tool and tool not in target.detected_by:
            target.detected_by.append(tool)
    if not target.fingerprint:
        target.fingerprint = other.fingerprint
    if not target.rule_id:
        target.rule_id = other.rule_id


def dedupe(findings: list[Finding]) -> list[Finding]:
    """Collapse exact and cross-tool duplicates; recompute stable ids.

    The input is sorted by location, then severity, then tool/rule/message, so
    the *survivor* of a merge is the highest-severity (then lexicographically
    first) reporter: it carries the category, rule and message of the merged
    issue, and every other reporter is folded into ``detected_by``. Recomputing
    ids at the end gives a merged finding one stable id, derived from the
    survivor, that is identical on every run of the same change set.
    """
    ordered = sorted(findings, key=_sort_key)
    unique: dict[tuple, Finding] = {}
    for finding in ordered:
        unique.setdefault(_exact_key(finding), finding)
    merged: list[Finding] = []
    for finding in ordered:
        if unique.get(_exact_key(finding)) is not finding:
            continue
        target = next((item for item in merged if _same_issue(item, finding)), None)
        if target is None:
            merged.append(finding)
        else:
            _merge_into(target, finding)
    for finding in merged:
        finding.id = stable_id(finding.file, finding.line, finding.category,
                               finding.original_message or finding.title)
    return merged
