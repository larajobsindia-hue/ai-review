"""Structural finding validation before the LLM's voice becomes a decision."""
from __future__ import annotations

from ai_review.models import ReviewResult, StagedChange

VALID_SEVERITY = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}


def _validate_static_links(result: ReviewResult) -> None:
    """Drop AI references to static findings that do not exist (spec §26/§27).

    A hallucinated ``finding_id`` must never render as "Detected by semgrep": the
    reference is removed and the AI finding itself is untouched. With static
    analysis disabled there are no known ids, so every reference is dropped.
    """
    known = {f.id for f in (result.static.findings if result.static else []) if f.id}
    result.static_assessments = [a for a in result.static_assessments
                                 if a.finding_id in known]
    for finding in result.issues:
        if finding.related_static_finding_id and \
                finding.related_static_finding_id not in known:
            finding.related_static_finding_id = None


def validate_findings(result: ReviewResult, changes: list[StagedChange]) -> ReviewResult:
    """Drop findings that do not anchor to a changed line in the staged set.

    Static-finding references (``related_static_finding_id`` and
    ``static_assessments``) are validated against the run's static findings
    first, so provenance can never be claimed for an id that does not exist.

    A finding survives only if its severity is known, its confidence is in
    [0.0, 1.0], and either it is a hard_block finding whose file is staged
    (line ignored) or its file is staged and its line falls inside the union
    of that file's hunks' changed_new_lines. A BLOCK decision is downgraded
    to WARN when no kept finding can justify blocking.

    Mutates ``result.issues`` (and possibly ``result.decision``) in place and
    returns the same object.
    """
    _validate_static_links(result)

    file_lines: dict[str, set[int]] = {}
    for change in changes:
        lines = file_lines.setdefault(change.path, set())
        for hunk in change.hunks:
            lines.update(hunk.changed_new_lines)

    kept = []
    for f in result.issues:
        if f.severity not in VALID_SEVERITY or not (0.0 <= f.confidence <= 1.0):
            continue
        if f.hard_block:
            if f.file in file_lines:
                kept.append(f)
            continue
        if f.file not in file_lines:
            continue
        if f.line is None:
            continue
        if f.line not in file_lines[f.file]:
            continue
        kept.append(f)

    result.issues = kept
    if result.decision == "BLOCK" and not any(
        f.severity in ("CRITICAL", "HIGH") or f.hard_block for f in kept
    ):
        result.decision = "WARN"
    return result
