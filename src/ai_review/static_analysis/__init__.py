"""Static-analysis layer: deterministic evidence for the AI reviewer.

Public API: :func:`run_static_analysis`, :func:`plan_static_analysis` (the
no-execution variant used by ``--dry-run``) and :func:`format_static_findings`.
Nothing in this package can block a commit: findings are handed to the LLM as
evidence and never inserted into ``ReviewResult.issues`` (design D1, spec §35).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from ai_review.config import StaticAnalysisConfig
from ai_review.models import (AnalyzerResult, Finding, StaticAnalysisSummary,
                              ToolResult)
from ai_review.static_analysis.base import StaticAnalysisError, StaticAnalyzer
from ai_review.static_analysis.context import (SCOPE_PROJECT, AnalysisContext,
                                               build_context, with_config)
from ai_review.static_analysis.deduplicator import dedupe
from ai_review.static_analysis.normalizer import SEVERITY_ORDER
from ai_review.static_analysis.registry import build_candidates

__all__ = [
    "StaticAnalysisError", "build_context", "format_static_findings",
    "plan_static_analysis", "run_static_analysis",
]

#: Prompt budget: findings shown to the LLM and characters per message.
MAX_PROMPT_FINDINGS = 25
MAX_PROMPT_MESSAGE_CHARS = 300


def _safe_analyze(analyzer: StaticAnalyzer, ctx: AnalysisContext) -> AnalyzerResult:
    """Run one analyzer; an analyzer bug becomes a failed tool, not a crash.

    A static-analysis layer that can abort a commit because a tool module has a
    bug would be strictly worse than no static analysis at all (spec §34).
    """
    try:
        return analyzer.analyze(ctx)
    except Exception as exc:                      # deliberate catch-all
        return AnalyzerResult(status="failed", error=f"{type(exc).__name__}: {exc}")


def _files_analyzed(analyzers: list[StaticAnalyzer], ctx: AnalysisContext) -> int:
    """Distinct staged files passed to at least one executed analyzer."""
    covered: set[str] = set()
    for analyzer in analyzers:
        if analyzer.scope == SCOPE_PROJECT:
            covered.update(ctx.changed_paths())
        else:
            covered.update(ctx.changed_paths(analyzer.extensions))
    return len(covered)


def run_static_analysis(cfg: StaticAnalysisConfig,
                        ctx: AnalysisContext) -> StaticAnalysisSummary:
    """Run every enabled, applicable analyzer concurrently and normalize results.

    Never raises for tool behaviour: a missing, timing-out, crashing or
    unparseable tool contributes a ``failed``/``unavailable`` ``ToolResult`` and
    no findings (spec §15/§16/§34). ``fail_on_error: true`` is the single
    explicit exception and raises :class:`StaticAnalysisError` (the CLI turns it
    into the standard ``error: ...`` + exit 2 contract).
    """
    ctx = with_config(ctx, cfg)
    started = time.monotonic()
    analyzers, tools = build_candidates(cfg, ctx)
    if analyzers:
        workers = max(1, min(cfg.concurrency, len(analyzers)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda analyzer: _safe_analyze(analyzer, ctx),
                                    analyzers))
    else:
        results = []

    findings: list[Finding] = []
    executed: list[StaticAnalyzer] = []
    for analyzer, result in zip(analyzers, results):
        tools.append(ToolResult(name=analyzer.name, status=result.status,
                                exit_code=result.exit_code, error=result.error,
                                duration_ms=result.duration_ms))
        if result.status == "run":
            executed.append(analyzer)
            # Per-tool cap *before* merging: a runaway tool cannot flood the prompt.
            findings.extend(result.findings[: cfg.max_findings])

    summary = StaticAnalysisSummary(
        findings=dedupe(findings),
        tools=sorted(tools, key=lambda tool: tool.name),
        files_analyzed=_files_analyzed(executed, ctx),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    if cfg.fail_on_error:
        failed = [tool for tool in summary.tools if tool.status == "failed"]
        if failed:
            raise StaticAnalysisError("static analysis failed: " + "; ".join(
                f"{tool.name}: {tool.error}" for tool in failed))
    return summary


def plan_static_analysis(cfg: StaticAnalysisConfig,
                         ctx: AnalysisContext) -> StaticAnalysisSummary:
    """What a real run *would* do, without executing anything.

    ``--dry-run`` must stay free of external processes, so the plan reports the
    enabled + applicable analyzers and their availability, and nothing else.
    """
    ctx = with_config(ctx, cfg)
    analyzers, tools = build_candidates(cfg, ctx)
    for analyzer in analyzers:
        available = analyzer.is_available(ctx)
        tools.append(ToolResult(
            name=analyzer.name, status="skipped",
            error="dry-run (not executed)" if available
            else f"{analyzer.executable or analyzer.name} not found",
        ))
    return StaticAnalysisSummary(findings=[], tools=sorted(tools, key=lambda t: t.name))


def format_static_findings(summary: StaticAnalysisSummary | None, *,
                           max_findings: int = MAX_PROMPT_FINDINGS,
                           max_message_chars: int = MAX_PROMPT_MESSAGE_CHARS) -> str:
    """Compact, truncated evidence block for the LLM prompt (spec §24/§38).

    One located finding per entry with severity, detecting tool(s), file:line,
    rule and the stable id (so the AI can reference it back). Never a raw dump,
    and never silently truncated: a cut list says how many findings were left
    out, because a missing marker reads as "nothing else to see".
    """
    if summary is None or not summary.findings:
        return ""
    ordered = sorted(summary.findings,
                     key=lambda f: (-SEVERITY_ORDER.get(f.severity, 0), f.file,
                                    f.line or 0, f.id or ""))
    shown = ordered[:max_findings]
    tools_run = sorted({tool.name for tool in summary.tools if tool.status == "run"})
    lines = [f"{len(summary.findings)} finding(s) from {len(tools_run)} tool(s): "
             + (", ".join(tools_run) or "none")]
    for finding in shown:
        message = " ".join((finding.original_message or finding.title).split())
        if len(message) > max_message_chars:
            message = message[:max_message_chars] + "…"
        by = ", ".join(finding.detected_by or ([finding.tool] if finding.tool else []))
        lines.append(f"- [{finding.severity}] {by} · {finding.file}:"
                     f"{finding.line or '?'} · rule {finding.rule_id or 'n/a'} · id {finding.id}")
        lines.append(f"  {message}")
    omitted = len(ordered) - len(shown)
    if omitted:
        lines.append(f"…[{omitted} further finding(s) omitted from this list]")
    return "\n".join(lines)
