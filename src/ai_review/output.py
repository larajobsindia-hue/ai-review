"""Terminal / JSON / Markdown output renderers."""
from __future__ import annotations

import json

from ai_review.models import RepoProfile, ReviewResult

_WHEN = "━" * 34


def _meta(meta: dict | None) -> dict:
    return meta or {}


def render(result: ReviewResult, *, fmt: str = "terminal",
           profile: RepoProfile | None = None, meta: dict | None = None) -> str:
    """Render *result* in the requested format (json | markdown | terminal).

    Unknown ``fmt`` values fall back to the terminal renderer; Task 14's CLI
    restricts choices at the argument boundary.
    """
    if fmt == "json":
        return render_json(result)
    if fmt == "markdown":
        return render_markdown(result, profile, meta)
    return render_terminal(result, profile, meta)


def render_terminal(result: ReviewResult, profile: RepoProfile | None,
                    meta: dict | None = None) -> str:
    """Boxed plain-text layout for the pre-commit hook.

    ``meta`` keys: repo, branch, files, added, removed, detected, checks
    (list[CheckResult] — note: JSON/markdown instead read ``result.checks``),
    duration_s. A non-empty summary is always rendered under the RESULT
    label: on BLOCK it is the policy- or failure-authored explanation and is
    the only reason the gate fired.
    """
    m = _meta(meta)
    lines = [_WHEN, " AI Git Review", _WHEN]
    if m.get("repo"):
        lines += [f"Repository: {m['repo']}", f"Branch: {m.get('branch', '')}"]
    if m.get("files") is not None:
        lines += [f"Changes: {m['files']} files  +{m.get('added', 0)} -{m.get('removed', 0)}"]
    if m.get("detected"):
        lines += ["Detected: " + ", ".join(m["detected"])]
    if m.get("duration_s") is not None:
        lines.append(f"Duration: {m['duration_s']:.1f}s")
    checks = m.get("checks") or []
    if checks:
        lines += [""]
        for c in checks:
            mark = "✓" if c.exit_code == 0 else "✗"
            lines.append(f"{mark} {c.name}")
    lines += ["", f"AI review... {'✓' if result.decision != 'BLOCK' else '✗'}"]
    lines += [_WHEN]
    label = {"PASS": "RESULT: PASS", "BLOCK": "RESULT: COMMIT BLOCKED", "WARN": "RESULT: WARNING"}
    lines += [f" {label.get(result.decision, f'RESULT: {result.decision}')}", _WHEN, ""]
    for f in result.issues:
        lines += [f"{f.severity} — {f.category}", "", f"{f.file}:{f.line or '?'}", "",
                  f.title, "", f"Confidence: {int(f.confidence * 100)}%", "",
                  "Recommendation:", f.recommendation, _WHEN, ""]
    if result.summary:
        lines.append(result.summary)
    return "\n".join(lines)


def render_json(result: ReviewResult) -> str:
    """Stable JSON document (decision/summary/issues/checks) for CI consumers."""
    return json.dumps({
        "decision": result.decision,
        "summary": result.summary,
        "issues": [
            {"severity": f.severity, "category": f.category, "file": f.file,
             "line": f.line, "title": f.title, "description": f.description,
             "evidence": f.evidence, "recommendation": f.recommendation,
             "confidence": f.confidence, "is_pre_existing": f.is_pre_existing}
            for f in result.issues
        ],
        "checks": [{"name": c.name, "command": c.command, "exit_code": c.exit_code}
                   for c in result.checks],
    }, indent=2)


def render_markdown(result: ReviewResult, profile: RepoProfile | None,
                    meta: dict | None = None) -> str:
    """Markdown report (heading, summary, optional Detected, Issues table, Checks)."""
    m = _meta(meta)
    out = [f"# AI Review: {result.decision}", "", result.summary, ""]
    if m.get("detected"):
        out += ["## Detected", ", ".join(m["detected"]), ""]
    out += ["## Issues", "",
            "| Severity | Category | File | Line | Confidence | Title |",
            "|---|---|---|---|---|---|"]
    for f in result.issues:
        title = f.title.replace("|", "\\|").replace("\n", " ")
        out.append(f"| {f.severity} | {f.category} | {f.file} | {f.line or '-'} "
                   f"| {f.confidence:.2f} | {title} |")
    out += ["", "## Checks", ""]
    for c in result.checks:
        out.append(f"- {'OK' if c.exit_code == 0 else 'FAIL'}: `{c.name}`")
    return "\n".join(out)
