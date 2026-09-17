"""Terminal / JSON / Markdown output renderers."""
from __future__ import annotations

import json

from ai_review.models import RepoProfile, ReviewResult

_WHEN = "━" * 34

#: Per-tool status marks shared by the terminal renderer.
_TOOL_MARKS = {"run": "✓", "unavailable": "⚠", "skipped": "·", "failed": "✗"}


def _meta(meta: dict | None) -> dict:
    return meta or {}


def _static_visible(result: ReviewResult) -> bool:
    """Render the static block only when there is something honest to say.

    Findings always qualify; otherwise a tool that actually ran (or failed) is
    worth reporting, while an all-``skipped`` layer (disabled, dry-run, no
    applicable files) stays silent so the pre-feature output is unchanged.
    """
    static = result.static
    if static is None:
        return False
    return bool(static.findings) or any(tool.status != "skipped" for tool in static.tools)


def _assessment_for(result: ReviewResult, finding_id: str | None) -> str:
    """The AI's verdict on a static finding, verbatim-in-prose (or "")."""
    for assessment in result.static_assessments:
        if assessment.finding_id == finding_id:
            return assessment.verdict.replace("_", " ")
    return ""


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
    if _static_visible(result):
        # Evidence, not a verdict: shown under its own heading so a tool report
        # can never be read as the reason the commit was gated.
        lines += [_WHEN, " Static Analysis", _WHEN,
                  f"{len(result.static.findings)} finding(s) from the tool layer:"]
        for tool in result.static.tools:
            mark = _TOOL_MARKS.get(tool.status, "·")
            note = f" — {tool.error}" if tool.error else ""
            lines.append(f"{mark} {tool.name}{note}")
        lines.append("")
        for f in result.static.findings:
            verdict = _assessment_for(result, f.id)
            lines += [f"{f.severity} — {f.tool or 'static'}  {f.file}:{f.line or '?'}",
                      f.title, f"Confidence: {int(f.confidence * 100)}%"]
            if f.detected_by:
                lines.append("Detected by " + ", ".join(f.detected_by))
            if verdict:
                lines.append(f"AI review: {verdict}")
            lines.append("")
    for f in result.issues:
        lines += [f"{f.severity} — {f.category}", "", f"{f.file}:{f.line or '?'}", "",
                  f.title, "", f"Confidence: {int(f.confidence * 100)}%", "",
                  "Recommendation:", f.recommendation, _WHEN, ""]
    if result.summary:
        lines.append(result.summary)
    return "\n".join(lines)


def render_json(result: ReviewResult) -> str:
    """Stable JSON document (decision/summary/issues/checks) for CI consumers.

    The static-analysis payload is attached only when the layer produced
    something, so a pre-feature consumer sees the exact same document.
    """
    data = {
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
    }
    if result.static is not None:
        data["static"] = {
            "findings": [
                {"id": f.id, "tool": f.tool, "rule_id": f.rule_id,
                 "severity": f.severity, "original_severity": f.original_severity,
                 "category": f.category, "file": f.file, "line": f.line,
                 "title": f.title, "description": f.description, "evidence": f.evidence,
                 "confidence": f.confidence, "fingerprint": f.fingerprint,
                 "detected_by": f.detected_by}
                for f in result.static.findings
            ],
            "tools": [{"name": t.name, "status": t.status, "exit_code": t.exit_code,
                       "error": t.error, "duration_ms": t.duration_ms}
                      for t in result.static.tools],
            "files_analyzed": result.static.files_analyzed,
            "duration_ms": result.static.duration_ms,
            "counts": result.static.severity_counts(),
        }
    if result.static_assessments:
        data["static_assessments"] = [
            {"finding_id": a.finding_id, "verdict": a.verdict, "reason": a.reason}
            for a in result.static_assessments]
    return json.dumps(data, indent=2)


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
    if _static_visible(result):
        out += ["", "## Static Analysis", "",
                "| Severity | Tool | File | Line | Rule | Title |",
                "|---|---|---|---|---|---|"]
        for f in result.static.findings:
            title = f.title.replace("|", "\\|").replace("\n", " ")
            out.append(f"| {f.severity} | {f.tool or '-'} | {f.file} | {f.line or '-'} "
                       f"| {f.rule_id or '-'} | {title} |")
        for tool in result.static.tools:
            if tool.status != "run":
                out.append(f"- {tool.name}: {tool.status}"
                           + (f" — {tool.error}" if tool.error else ""))
    out += ["", "## Checks", ""]
    for c in result.checks:
        out.append(f"- {'OK' if c.exit_code == 0 else 'FAIL'}: `{c.name}`")
    return "\n".join(out)
