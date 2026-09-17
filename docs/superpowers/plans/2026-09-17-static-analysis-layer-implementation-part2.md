# Static Analysis Layer Implementation Plan — Part 2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**This is Part 2 of 2.** Part 1 (`docs/superpowers/plans/2026-09-17-static-analysis-layer-implementation.md`) holds the goal, architecture, global constraints, the locked design-doc resolutions **RD-1…RD-11**, and framework Tasks 1–8 (models, config, context, runner, normalizer, deduplicator, base, registry). It ends at the `<!-- PLAN-CONTINUES -->` marker.

Part 2 continues with Tasks 9–16 (public API → pipeline wiring → CLI → output → doctor → docs), then the analyzer phases (SA-2/SA-3/SA-4) and the phase exit criteria. Task numbering, step conventions (`- [ ] **Step N: …**`, Run/Expected, commit) and the constraint set are unchanged from Part 1 — read it before starting here.

> **Erratum (SA-2 preamble).** The `parse_fixture` snippet that appears just before `tests/unit/test_static_analyzers.py` is a rejected sketch: **no** fixture helper ships in `runner.py` or anywhere else in `ai_review`. Fixture reading is test-only — the test modules define their own `_fixture`/`FIXTURES` helpers (shown directly below the sketch).

---

## Task 9: `__init__.py` — public API (`run_static_analysis`, `plan_static_analysis`, `format_static_findings`)

**Files:**
- Modify: `src/ai_review/static_analysis/__init__.py`
- Test: `tests/unit/test_static_run.py`

**Interfaces:**
- Consumes: `base`, `context`, `deduplicator`, `normalizer`, `registry`.
- Produces: `run_static_analysis(cfg, ctx)`, `plan_static_analysis(cfg, ctx)`, `format_static_findings(summary, *, max_findings, max_message_chars)`, `MAX_PROMPT_FINDINGS`, `MAX_PROMPT_MESSAGE_CHARS`, re-exported `StaticAnalysisError`, `build_context`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_static_run.py`:

```python
"""The static-analysis entry points: aggregation, isolation, prompt formatting."""
import pytest

from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import AnalyzerResult, ProfileEntry, RepoProfile, StagedChange
from ai_review.static_analysis import (format_static_findings, plan_static_analysis,
                                       registry, run_static_analysis)
from ai_review.static_analysis.base import StaticAnalysisError, StaticAnalyzer
from ai_review.static_analysis.context import build_context
from ai_review.static_analysis.normalizer import make_finding

PROFILE = RepoProfile(languages=[ProfileEntry("Python", 0.9, [])])


class StubAnalyzer(StaticAnalyzer):
    """Analyzer with stubbed execution: no subprocess, fully offline."""
    name = "stub"
    executable = "stub"
    extensions = (".py",)

    def build_argv(self, exe, ctx):
        return [exe]

    def parse(self, run, ctx):
        return []

    def analyze(self, ctx):
        return AnalyzerResult(
            findings=[make_finding(tool=self.name, severity="HIGH", rule_id="stub.rule",
                                   file="a.py", line=1, message="Static stub finding.")],
            status="run", exit_code=1, duration_ms=7)


class BoomAnalyzer(StubAnalyzer):
    name = "boom"

    def analyze(self, ctx):
        raise RuntimeError("kaboom")


def _ctx(tmp_path, *tools, changes=None):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = AppConfig()
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig() for name in tools}
    return cfg, build_context(str(tmp_path), cfg, changes if changes is not None else
                              [StagedChange(path="a.py", status="modified")], PROFILE)


def test_run_aggregates_tools_and_findings(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path, "stub")
    summary = run_static_analysis(cfg.static_analysis, ctx)
    assert [t.name for t in summary.tools] == ["stub"]
    assert summary.tools[0].status == "run" and summary.tools[0].exit_code == 1
    assert len(summary.findings) == 1
    assert summary.findings[0].tool == "stub" and summary.findings[0].id
    assert summary.files_analyzed == 1
    assert summary.severity_counts()["HIGH"] == 1


def test_analyzer_crash_is_isolated_and_reported(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    monkeypatch.setitem(registry.REGISTRY, "boom", BoomAnalyzer)
    cfg, ctx = _ctx(tmp_path, "stub", "boom")
    summary = run_static_analysis(cfg.static_analysis, ctx)
    assert {t.name: t.status for t in summary.tools} == {"stub": "run", "boom": "failed"}
    assert "kaboom" in next(t.error for t in summary.tools if t.name == "boom")
    assert [f.tool for f in summary.findings] == ["stub"]


def test_unconfigured_tool_never_runs(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path)
    summary = run_static_analysis(cfg.static_analysis, ctx)
    assert summary.findings == [] and summary.tools == []


def test_fail_on_error_raises_only_when_opted_in(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "boom", BoomAnalyzer)
    cfg, ctx = _ctx(tmp_path, "boom")
    run_static_analysis(cfg.static_analysis, ctx)            # default: tolerant
    cfg.static_analysis.fail_on_error = True
    with pytest.raises(StaticAnalysisError) as excinfo:
        run_static_analysis(cfg.static_analysis, ctx)
    assert "boom" in str(excinfo.value)


def test_plan_static_analysis_executes_nothing(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path, "stub")
    planned = plan_static_analysis(cfg.static_analysis, ctx)
    assert planned.findings == []
    assert [t.status for t in planned.tools] == ["skipped"]
    assert "dry-run" in planned.tools[0].error


def test_files_analyzed_counts_only_covered_staged_files(tmp_path, monkeypatch):
    (tmp_path / "b.js").write_text("let x = 1\n")
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path, "stub", changes=[
        StagedChange(path="a.py", status="modified"),
        StagedChange(path="b.js", status="modified"),
    ])
    assert run_static_analysis(cfg.static_analysis, ctx).files_analyzed == 1


def test_format_static_findings_is_empty_without_findings(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    cfg, ctx = _ctx(tmp_path)
    assert format_static_findings(run_static_analysis(cfg.static_analysis, ctx)) == ""
    assert format_static_findings(None) == ""


def test_format_static_findings_orders_truncates_and_marks_omissions(tmp_path, monkeypatch):
    class Loud(StubAnalyzer):
        name = "loud"

        def analyze(self, ctx):
            return AnalyzerResult(
                findings=[make_finding(tool=self.name, severity=sev, file="a.py", line=i + 1,
                                       message="A very long message " * 40)
                          for i, sev in enumerate(["LOW", "CRITICAL", "MEDIUM", "HIGH"])],
                status="run")

    monkeypatch.setitem(registry.REGISTRY, "loud", Loud)
    cfg, ctx = _ctx(tmp_path, "loud")
    text = format_static_findings(run_static_analysis(cfg.static_analysis, ctx),
                                  max_findings=2, max_message_chars=50)
    assert text.startswith("4 finding(s) from 1 tool(s): loud")
    assert text.index("[CRITICAL]") < text.index("[HIGH]")           # severity ordered
    assert "…" in text                                                # message truncated
    assert "[2 further finding(s) omitted from this list]" in text
    assert text.count("\n- [") == 2                                   # budget respected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_static_run.py -q`
Expected: FAIL — `ImportError: cannot import name 'run_static_analysis' from 'ai_review.static_analysis'`

- [ ] **Step 3: Implement**

`src/ai_review/static_analysis/__init__.py`:

```python
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
    """What a real run *would* do, without executing anything (RD-7).

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_static_run.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/static_analysis/__init__.py tests/unit/test_static_run.py
git commit -m "feat: static-analysis entry points (run, plan, prompt formatting)"
```

## Task 10: Prompt section, LLM schema, parser and system instructions

**Files:**
- Modify: `src/ai_review/prompts.py` (`PromptBuilder.build`, `OUTPUT_JSON_SCHEMA`)
- Modify: `src/ai_review/prompts/system.md`
- Modify: `src/ai_review/parser.py`
- Test: `tests/unit/test_static_prompt.py`, `tests/unit/test_parser.py` (extend)

**Interfaces:**
- Consumes: `models.{RepoProfile, StagedChange, StaticAnalysisSummary, StaticAssessment}`, Task 9's `format_static_findings`.
- Produces: `PromptBuilder.build(..., static_text: str = "")`; `OUTPUT_JSON_SCHEMA` gains an optional `related_static_finding_id` per issue and a top-level `static_assessments` array; `parse_llm_json` populates `ReviewResult.static_assessments` and each finding's `related_static_finding_id`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_static_prompt.py`:

```python
"""The static evidence block reaches the LLM prompt in the documented shape."""
from ai_review.models import (RepoProfile, StagedChange, StaticAnalysisSummary,
                              ToolResult)
from ai_review.prompts import OUTPUT_JSON_SCHEMA, PromptBuilder, default_prompt_dir
from ai_review.static_analysis import format_static_findings
from ai_review.static_analysis.normalizer import make_finding


def _payload(static_text=""):
    builder = PromptBuilder(default_prompt_dir())
    return builder.build(profile=RepoProfile(),
                         changes=[StagedChange(path="a.py", status="modified")],
                         diff_text="+x = 1\n", context_text="", static_text=static_text)


def test_static_section_is_added_when_evidence_exists():
    finding = make_finding(tool="semgrep", severity="HIGH",
                           rule_id="php.security.sql-injection",
                           file="app/Models/User.php", line=42,
                           message="Potential SQL injection.")
    summary = StaticAnalysisSummary(findings=[finding],
                                    tools=[ToolResult(name="semgrep", status="run")])
    payload = _payload(format_static_findings(summary))
    assert "# Static Analysis Findings" in payload.user
    assert "app/Models/User.php:42" in payload.user
    assert f"id {finding.id}" in payload.user
    assert payload.user.index("# Static Analysis Findings") < \
        payload.user.index("# Output Format")


def test_static_section_is_absent_when_there_is_no_evidence():
    assert "# Static Analysis Findings" not in _payload("").user
    assert "# Static Analysis Findings" not in _payload("   ").user


def test_existing_prompt_sections_are_unchanged():
    user = _payload().user
    for section in ("# Staged Files", "# Staged Diff", "# Relevant Context", "# Output Format"):
        assert section in user


def test_schema_exposes_static_assessments_and_related_ids():
    issue = OUTPUT_JSON_SCHEMA["properties"]["issues"]["items"]["properties"]
    assert issue["related_static_finding_id"]["type"] == ["string", "null"]
    assert "related_static_finding_id" not in \
        OUTPUT_JSON_SCHEMA["properties"]["issues"]["items"]["required"]
    assessments = OUTPUT_JSON_SCHEMA["properties"]["static_assessments"]
    assert assessments["items"]["properties"]["verdict"]["enum"] == [
        "confirmed", "likely_true", "uncertain", "likely_false_positive", "false_positive"]
    assert OUTPUT_JSON_SCHEMA["required"] == ["decision", "summary", "issues"]
```

Append to `tests/unit/test_parser.py`:

```python
STATIC_ANSWER = json.dumps({
    "decision": "WARN", "summary": "checked",
    "issues": [{"severity": "HIGH", "category": "SECURITY", "file": "app/x.php", "line": 3,
                "title": "t", "description": "d", "evidence": "e", "recommendation": "r",
                "confidence": 0.9, "is_pre_existing": False,
                "related_static_finding_id": "abc123"}],
    "static_assessments": [{"finding_id": "abc123", "verdict": "confirmed",
                            "reason": "reachable"}],
})


def test_parse_static_assessments_and_links():
    result = parse_llm_json(STATIC_ANSWER)
    assert result.issues[0].related_static_finding_id == "abc123"
    assert result.static_assessments[0].finding_id == "abc123"
    assert result.static_assessments[0].verdict == "confirmed"
    assert result.static_assessments[0].reason == "reachable"


def test_parse_is_backward_compatible_without_static_fields():
    result = parse_llm_json('{"decision": "PASS", "summary": "ok", "issues": []}')
    assert result.static_assessments == []
    assert result.issues == []


def test_parse_rejects_unknown_verdict():
    bad = json.dumps({"decision": "PASS", "summary": "s", "issues": [],
                      "static_assessments": [{"finding_id": "a", "verdict": "maybe"}]})
    with pytest.raises(ParseError):
        parse_llm_json(bad)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_static_prompt.py tests/unit/test_parser.py -q`
Expected: FAIL — `TypeError: build() got an unexpected keyword argument 'static_text'`

- [ ] **Step 3: Implement**

`src/ai_review/prompts.py` — in `OUTPUT_JSON_SCHEMA`, add the optional link to the issue properties:

```python
                    "is_pre_existing": {"type": "boolean"},
                    "related_static_finding_id": {"type": ["string", "null"]},
                },
```

(the issue `required` list is unchanged so existing grammars keep validating) and add a top-level property after the `"issues"` block:

```python
        "static_assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                    "verdict": {"enum": [
                        "confirmed", "likely_true", "uncertain",
                        "likely_false_positive", "false_positive",
                    ]},
                    "reason": {"type": "string"},
                },
                "required": ["finding_id", "verdict", "reason"],
            },
        },
```

Change `PromptBuilder.build` to accept and render evidence:

```python
    def build(self, profile: RepoProfile, changes: list[StagedChange],
              diff_text: str, context_text: str, rules: str = "",
              tech_template: str | None = None,
              max_diff_chars: int = MAX_DIFF_CHARS,
              static_text: str = "") -> PromptPayload:
```

and inside, after `user_parts` is assembled (before the repository-rules branch):

```python
        if static_text and static_text.strip():
            # Deterministic tool evidence (design D1): a bounded, severity-ordered
            # block. It is evidence for the AI to verify, never instructions it
            # must obey, so it is labelled as findings rather than requirements.
            user_parts += ["# Static Analysis Findings", static_text.strip()]
```

`src/ai_review/prompts/system.md` — append:

```text
Static-analysis findings may be supplied below as EVIDENCE, not as truth.

For each static finding you rely on:
- verify it against the supplied source before repeating it as a finding
- classify false positives explicitly, including why the code is safe
- never present a static finding as your own discovery: reference its id
  (`related_static_finding_id`) and return a `static_assessments` verdict

Also report real problems static-analysis tools cannot detect: business-logic
errors, architectural problems, incorrect assumptions, API contract problems,
concurrency issues, maintainability problems, missing validation, and incorrect
error handling.
```

`src/ai_review/parser.py` — add the verdict vocabulary and the assessment model:

```python
VALID_VERDICTS = frozenset({
    "confirmed", "likely_true", "uncertain", "likely_false_positive", "false_positive",
})


class _AssessmentModel(BaseModel):
    finding_id: str
    verdict: str
    reason: str = ""
```

add `related_static_finding_id: str | None = None` to `_IssueModel`, add to `_ReviewModel`:

```python
    static_assessments: list[_AssessmentModel] = Field(default_factory=list)
```

and in `parse_llm_json`, after the existing severity loop:

```python
    for assessment in model.static_assessments:
        if assessment.verdict not in VALID_VERDICTS:
            raise ParseError(f"invalid static verdict {assessment.verdict!r}")
```

pass the link through when building findings (`related_static_finding_id=i.related_static_finding_id`) and return:

```python
    return ReviewResult(
        decision=model.decision, summary=model.summary, issues=findings,
        static_assessments=[
            StaticAssessment(finding_id=a.finding_id, verdict=a.verdict, reason=a.reason)
            for a in model.static_assessments
        ],
    )
```

with `StaticAssessment` added to the existing `from ai_review.models import ...` line.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_static_prompt.py tests/unit/test_parser.py tests/unit/test_prompts.py tests/unit/test_package_data.py -q`
Expected: PASS

- [ ] **Step 5: Verify the whole suite (a prompt asset changed)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ai_review/prompts.py src/ai_review/prompts/system.md src/ai_review/parser.py \
        tests/unit/test_static_prompt.py tests/unit/test_parser.py
git commit -m "feat: feed static evidence to the LLM; parse its static verdicts"
```

## Task 11: Validator — static-id link validation

**Files:**
- Modify: `src/ai_review/validator.py`
- Test: `tests/unit/test_validator.py` (extend)

**Interfaces:**
- Consumes: `ReviewResult.static`, `ReviewResult.static_assessments`, `Finding.related_static_finding_id`.
- Produces: `validate_findings` additionally drops assessments/links whose `finding_id` is unknown. Signature unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_validator.py`:

```python
from ai_review.models import (Finding, ReviewResult, StaticAnalysisSummary,
                              StaticAssessment, ToolResult)


class _Hunk:
    changed_new_lines = {3}


class _Change:
    path = "a.py"
    hunks = [_Hunk()]


def _static_finding(finding_id):
    return Finding(severity="LOW", category="BUG", file="a.py", line=3, title="s",
                   description="d", evidence="e", recommendation="r", confidence=0.6,
                   source="static_analysis", id=finding_id, tool="semgrep")


def _result(link="abc123", assessments=(("abc123", "confirmed"), ("nope", "uncertain"))):
    issues = [Finding(severity="HIGH", category="BUG", file="a.py", line=3, title="t",
                      description="d", evidence="e", recommendation="r", confidence=0.9,
                      related_static_finding_id=link)]
    static = StaticAnalysisSummary(findings=[_static_finding("abc123")],
                                   tools=[ToolResult(name="semgrep", status="run")])
    return ReviewResult(decision="BLOCK", summary="s", issues=issues, static=static,
                        static_assessments=[StaticAssessment(finding_id=fid, verdict=verdict)
                                            for fid, verdict in assessments])


def test_unknown_static_link_is_dropped_but_the_issue_survives():
    result = validate_findings(_result(link="ghost"), [_Change()])
    assert result.issues[0].related_static_finding_id is None
    assert len(result.issues) == 1


def test_known_static_link_survives():
    result = validate_findings(_result(), [_Change()])
    assert result.issues[0].related_static_finding_id == "abc123"


def test_unknown_assessments_are_dropped():
    result = validate_findings(_result(), [_Change()])
    assert [a.finding_id for a in result.static_assessments] == ["abc123"]


def test_links_are_cleared_when_static_analysis_is_off():
    result = _result()
    result.static = None
    result = validate_findings(result, [_Change()])
    assert result.issues[0].related_static_finding_id is None
    assert result.static_assessments == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_validator.py -q`
Expected: FAIL — the ghost link and the unknown assessment both survive

- [ ] **Step 3: Implement**

`src/ai_review/validator.py` — add the helper and call it first in `validate_findings`:

```python
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
```

```python
def validate_findings(result: ReviewResult, changes: list[StagedChange]) -> ReviewResult:
    _validate_static_links(result)
    ...  # existing body unchanged
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_validator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/validator.py tests/unit/test_validator.py
git commit -m "feat: validate AI links to static findings"
```

## Task 12: Pipeline wiring (normal run, dry-run plan, `--static-only`)

**Files:**
- Modify: `src/ai_review/pipeline.py`
- Test: `tests/integration/test_pipeline.py` (extend)

**Interfaces:**
- Consumes: `static_analysis.{run_static_analysis, plan_static_analysis, format_static_findings, build_context}`.
- Produces: `RunnerOptions.static_only`; `build_pipeline(..., static_only=False)`; `Pipeline.run()` sets `ReviewResult.static` on every path; dry-run prints the planned analyzers; `static_only` never touches the provider.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_pipeline.py`:

```python
from ai_review.config import StaticAnalysisToolConfig
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.normalizer import make_finding
from ai_review.static_analysis.runner import ToolRun


class FakeStaticAnalyzer(StaticAnalyzer):
    """Registered stand-in for an installed tool (no binary needed offline)."""
    name = "fake-static"
    executable = "fake-static"
    ok_exit_codes = (0,)

    def build_argv(self, exe, ctx):
        return [exe]

    def parse(self, run, ctx):
        return [make_finding(tool=self.name, severity="HIGH", rule_id="fake.rule",
                             file="app.py", line=1, message="Static fake finding.")]


@pytest.fixture
def static_enabled(monkeypatch):
    """Enable the fake analyzer end to end: registered + resolvable + fake run."""
    monkeypatch.setitem(registry.REGISTRY, "fake-static", FakeStaticAnalyzer)
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: ToolRun(argv=list(argv), status="run",
                                                   exit_code=0, stdout="[]"))
    cfg = AppConfig()
    cfg.static_analysis.tools = {"fake-static": StaticAnalysisToolConfig()}
    return cfg


def test_static_findings_reach_the_llm_payload(repo, static_enabled):
    _stage(repo, "app.py")
    rec = RecordingProvider()
    result = build_pipeline(str(repo), static_enabled, provider=rec).run()
    assert result.static is not None
    assert [f.tool for f in result.static.findings] == ["fake-static"]
    assert "# Static Analysis Findings" in rec.payloads[0].user
    assert "fake-static" in rec.payloads[0].user
    assert "Static fake finding." in rec.payloads[0].user


def test_static_findings_do_not_gate_the_policy(repo, static_enabled):
    _stage(repo, "app.py")
    result = build_pipeline(str(repo), static_enabled, provider=StaticProvider([GOOD])).run()
    assert result.decision == "PASS"          # HIGH tool finding, not a hard block
    assert result.issues == []


def test_static_analysis_disabled_keeps_the_old_prompt(repo, static_enabled):
    _stage(repo, "app.py")
    static_enabled.static_analysis.enabled = False
    rec = RecordingProvider()
    result = build_pipeline(str(repo), static_enabled, provider=rec).run()
    assert result.static is None
    assert "# Static Analysis Findings" not in rec.payloads[0].user


def test_static_only_never_calls_the_provider(repo, static_enabled):
    _stage(repo, "app.py")
    pipe = build_pipeline(str(repo), static_enabled,
                          provider=DeadProvider(LlamaServerNotFound("must not be called")),
                          static_only=True)
    result = pipe.run()
    assert pipe.last_session is None
    assert result.static is not None
    assert result.decision == "PASS"
    assert "skipped" in result.summary.lower()


def test_static_only_still_blocks_on_a_staged_secret(repo, static_enabled):
    _stage(repo, "creds.py", f'token="{TOKEN}"\n')
    result = build_pipeline(str(repo), static_enabled,
                            provider=DeadProvider(LlamaServerNotFound("x")),
                            static_only=True).run()
    assert result.decision == "BLOCK"
    assert any(f.hard_block for f in result.issues)


def test_dry_run_plans_static_analysis_without_running_it(repo, static_enabled):
    _stage(repo, "app.py")
    called = []
    plan = build_pipeline(str(repo), static_enabled,
                          provider=NeverProvider(), dry_run=True).run()
    assert "static analysis (planned: fake-static)" in plan
    assert "[NOT run in dry-run]" in plan
    assert called == []                        # no provider, and no tool process
```

> `_stage` helper (`def _stage(root, name, content="x = 1\n")`) exists in `tests/unit/test_cli.py`; add the same three-line helper to the integration module (it is not importable across test modules).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_pipeline.py -q`
Expected: FAIL — `TypeError: build_pipeline() got an unexpected keyword argument 'static_only'`

- [ ] **Step 3: Implement**

`src/ai_review/pipeline.py`:

1. Update the module docstring's run sequence to insert the static step:

```text
-> classify -> static analysis (enabled analyzers, evidence only) -> diff text
-> redact -> scan_staged security findings -> resolution gate
```

2. Add imports:

```python
from ai_review.models import ReviewResult
from ai_review.static_analysis import (build_context, format_static_findings,
                                       plan_static_analysis, run_static_analysis)
```

3. `RunnerOptions` / `build_pipeline`:

```python
@dataclass
class RunnerOptions:
    repo_dir: str
    cfg: AppConfig
    provider: object
    dry_run: bool = False
    #: Offline-only mode: run the offline stages (static + security + resolution)
    #: and report them, never invoking the provider (design D7).
    static_only: bool = False
    verbose: bool = False
    select_only: object = None  # Phase 4: partial-review selection hook
    progress: object = None  # callable(dict) fired just before the LLM call
    meta: dict = field(default_factory=dict)
```

```python
def build_pipeline(
    repo_dir: str,
    cfg: AppConfig | None = None,
    *,
    provider: object | None = None,
    dry_run: bool = False,
    static_only: bool = False,
    verbose: bool = False,
    select_only: object | None = None,
    progress: object | None = None,
) -> Pipeline:
```

passing `static_only=static_only` into `RunnerOptions(...)`.

4. `run()`:

```python
        (meta, secret_kinds, security_findings, resolution_findings, profile,
         changes, redacted, static_summary) = self._prepare()

        if self.opts.dry_run:
            self.opts.meta = meta
            return self._dry_run_report(meta, secret_kinds, static_summary)

        cfg = self.opts.cfg
        failed = False
        message = ""
        if self.opts.static_only:
            # Offline-only report: the identical offline stages run, the provider
            # is never constructed or called, and the summary says so honestly.
            result = ReviewResult(
                decision="PASS",
                summary="AI review was skipped (--static-only).",
                static=static_summary,
            )
        else:
            if self.opts.progress is not None:
                self.opts.progress({...unchanged...})
            started = time.monotonic()
            try:
                result = self._review(profile, changes, redacted, static_summary)
            except (LlamaServerNotFound, ProviderError, ParseError) as exc:
                ...
            result.static = static_summary
```

so that `result.static` is set on the success path and on the failure path (the `FailureDecision` result) — `validate_findings` needs it to resolve AI links (Task 11).

5. `_prepare(self, *, plan_only: bool = False)` — after the security/resolution stages, before `meta`:

```python
        static_summary = None
        if cfg.static_analysis.enabled:
            ctx = build_context(root, cfg, changes, profile, diff_text)
            static_summary = (plan_static_analysis(cfg.static_analysis, ctx) if plan_only
                              else run_static_analysis(cfg.static_analysis, ctx))
```

and return it as the 8th element. Update its docstring to the new 8-tuple.

6. `dry_run()` uses the planning path:

```python
    def dry_run(self) -> str:
        """The same plan text as ``run()`` with ``dry_run=True``, without the flag."""
        meta, secret_kinds, _, _, _, _, _, static_summary = self._prepare(plan_only=True)
        self.opts.meta = meta
        return self._dry_run_report(meta, secret_kinds, static_summary)
```

and `run()`'s dry-run branch calls `self._prepare(plan_only=True)`.

7. `_dry_run_report(meta, secret_kinds, static_summary)` — insert the static step (and renumber the following steps) :

```python
        planned = ", ".join(tool.name for tool in static_summary.tools) or "none configured"
        ...
            "  4. static analysis (planned: " + planned + ")  [NOT run in dry-run]",
            "  5. redact secrets (" + redaction + ")",
            "  6. secret scan",
            "  7. resolution gate (unresolved references / missing imports)",
            "  8. build layered prompt",
            "  9. call LLM via configured provider  [SKIPPED in dry-run]",
            " 10. parse + validate findings",
            " 11. policy decision",
```

8. `_review(profile, changes, redacted, static_summary)` passes the evidence through:

```python
        payload = builder.build(
            profile=profile, changes=changes, diff_text=redacted,
            context_text=self._context_text(changes),
            max_diff_chars=self.opts.cfg.review.max_diff_kb * 1024,
            static_text=format_static_findings(static_summary),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration -q`
Expected: PASS (all pre-existing integration tests still pass because `AppConfig()` enables no tools)

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ai_review/pipeline.py tests/integration/test_pipeline.py
git commit -m "feat: wire static analysis into the pipeline (normal, dry-run, static-only)"
```

## Task 13: CLI flags (`--no-static-analysis`, `--static-only`)

**Files:**
- Modify: `src/ai_review/cli.py`
- Test: `tests/unit/test_cli.py` (extend **and** update the existing `test_config_overrides_reach_build_pipeline` fake)

**Interfaces:**
- Consumes: Task 12's `build_pipeline(..., static_only=...)`.
- Produces: two flags; `--no-static-analysis` folds `static_analysis.enabled=False` into the CLI override layer; `--static-only` builds no provider at all.

- [ ] **Step 1: Update the existing fake and write the failing tests**

In `tests/unit/test_cli.py`, `test_config_overrides_reach_build_pipeline` monkeypatches `build_pipeline` with a fake whose keyword list must now include the new flag:

```python
    def fake_build(repo_dir, cfg, *, provider=None, dry_run=False, static_only=False,
                   verbose=False, progress=None):
        captured.update(repo_dir=repo_dir, cfg=cfg, provider=provider,
                        dry_run=dry_run, static_only=static_only,
                        verbose=verbose, progress=progress)
        return RecordingPipe()
```

and add `assert captured["static_only"] is False` to that test. Then append:

```python
def test_no_static_analysis_flag_disables_it(repo, monkeypatch):
    _stage(repo, "app.py")
    captured = {}

    def fake_build(repo_dir, cfg, **kwargs):
        captured["cfg"] = cfg
        return RecordingPipe()

    monkeypatch.setattr("ai_review.cli.build_pipeline", fake_build)
    monkeypatch.setattr("ai_review.providers.make_provider", lambda cfg: object())
    assert main(["--staged", "--no-static-analysis"]) == EXIT_OK
    assert captured["cfg"].static_analysis.enabled is False


def test_static_only_never_builds_a_provider(repo, monkeypatch, capsys):
    _stage(repo, "app.py")

    def _boom(cfg):
        raise AssertionError("make_provider must not be called on --static-only")

    monkeypatch.setattr("ai_review.providers.make_provider", _boom)
    rc = main(["--static-only"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "RESULT: PASS" in out
    assert "skipped (--static-only)" in out


def test_static_only_still_exits_one_on_an_offline_hard_block(repo, monkeypatch):
    _stage(repo, "creds.py", 'token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"\n')
    monkeypatch.setattr("ai_review.providers.make_provider",
                        lambda cfg: (_ for _ in ()).throw(AssertionError("no provider")))
    assert main(["--static-only"]) == EXIT_BLOCK


def test_dry_run_wins_over_static_only(repo, capsys):
    _stage(repo, "app.py")
    rc = main(["--dry-run", "--static-only"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "dry-run" in out                       # the plan text, not a report
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_cli.py -q`
Expected: FAIL — `unrecognized arguments: --no-static-analysis` / `--static-only`

- [ ] **Step 3: Implement**

In `build_parser()`:

```python
    p.add_argument("--static-only", action="store_true",
                   help="run offline checks only (static + security + resolution), skip the LLM")
    p.add_argument("--no-static-analysis", action="store_true",
                   help="disable the static-analysis layer for this run")
```

In `main()`, inside the override try-block:

```python
        if args.no_static_analysis:
            overrides.setdefault("static_analysis", {})["enabled"] = False
```

and in the execution block:

```python
        from ai_review.providers import make_provider
        # Both offline modes must not construct a provider at all: --dry-run
        # (design: no external calls) and --static-only (no LLM by definition).
        provider = None if (args.dry_run or args.static_only) else make_provider(cfg)
        pipe = build_pipeline(repo_dir, cfg, provider=provider,
                              dry_run=args.dry_run, static_only=args.static_only,
                              verbose=args.verbose,
                              progress=_print_reviewing_notice)
```

The post-run branch is unchanged: `--dry-run` prints the plan text; `--static-only` falls through to `render(...)` and the `EXIT_OK`/`EXIT_BLOCK` mapping, so a staged secret still exits 1 and a tool problem never exits 2 (unless `fail_on_error`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/cli.py tests/unit/test_cli.py
git commit -m "feat: --static-only and --no-static-analysis CLI flags"
```

## Task 14: Output renderers (terminal, JSON, Markdown)

**Files:**
- Modify: `src/ai_review/output.py`
- Test: `tests/unit/test_output.py` (extend)

**Interfaces:**
- Consumes: `ReviewResult.static`, `ReviewResult.static_assessments`.
- Produces: an extra `Static Analysis` block (terminal), a `static` object + `static_assessments` array (JSON, only when present), and a `## Static Analysis` section (Markdown). `render`'s signature is unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_output.py`:

```python
from ai_review.models import (Finding, ReviewResult, StaticAnalysisSummary,
                              StaticAssessment, ToolResult)


def _static_result():
    static_finding = Finding(severity="HIGH", category="SECURITY", file="app/x.php", line=42,
                             title="SQL injection", description="unsanitized input",
                             evidence="rule: php.security.sql-injection",
                             recommendation="parameterize", confidence=0.9,
                             source="static_analysis", id="abc123", tool="semgrep",
                             rule_id="php.security.sql-injection",
                             original_severity="ERROR", original_message="Potential SQL injection.",
                             detected_by=["phpstan", "semgrep"])
    return ReviewResult(
        decision="PASS", summary="ok", issues=[],
        static=StaticAnalysisSummary(
            findings=[static_finding],
            tools=[ToolResult(name="semgrep", status="run", exit_code=1, duration_ms=12),
                   ToolResult(name="ruff", status="unavailable", error="not found")]),
        static_assessments=[StaticAssessment(finding_id="abc123", verdict="confirmed",
                                             reason="reachable")])


def test_terminal_renders_static_block_and_tool_states():
    text = render_terminal(_static_result(), profile=None, meta={})
    assert "Static Analysis" in text
    assert "✓ semgrep" in text
    assert "⚠ ruff — not found" in text
    assert "app/x.php:42" in text
    assert "SQL injection" in text
    assert "RESULT: PASS" in text          # the tool finding never changed the decision


def test_terminal_omits_the_block_without_static_analysis():
    text = render_terminal(ReviewResult(decision="PASS", summary="ok"), profile=None, meta={})
    assert "Static Analysis" not in text


def test_json_includes_static_shape_only_when_present():
    data = json.loads(render_json(_static_result()))
    assert data["static"]["findings"][0]["id"] == "abc123"
    assert data["static"]["findings"][0]["detected_by"] == ["phpstan", "semgrep"]
    assert data["static"]["findings"][0]["original_severity"] == "ERROR"
    assert data["static"]["tools"][0]["name"] == "semgrep"
    assert data["static"]["counts"]["HIGH"] == 1
    assert data["static_assessments"] == [
        {"finding_id": "abc123", "verdict": "confirmed", "reason": "reachable"}]
    plain = json.loads(render_json(ReviewResult(decision="PASS", summary="ok")))
    assert "static" not in plain and "static_assessments" not in plain


def test_markdown_has_static_section():
    md = render_markdown(_static_result(), profile=None, meta={})
    assert "## Static Analysis" in md
    assert "| HIGH | semgrep | app/x.php | 42 |" in md
    plain = render_markdown(ReviewResult(decision="PASS", summary="ok"), profile=None, meta={})
    assert "## Static Analysis" not in plain
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_output.py -q`
Expected: FAIL — no `Static Analysis` block / no `static` key

- [ ] **Step 3: Implement**

In `src/ai_review/output.py`:

```python
_TOOL_MARKS = {"run": "✓", "unavailable": "⚠", "skipped": "·", "failed": "✗"}


def _static_visible(result: ReviewResult) -> bool:
    """Render the block only when there is something honest to say."""
    static = result.static
    if static is None:
        return False
    return bool(static.findings) or any(tool.status != "skipped" for tool in static.tools)


def _assessment_for(result: ReviewResult, finding_id: str | None) -> str:
    for assessment in result.static_assessments:
        if assessment.finding_id == finding_id:
            return assessment.verdict.replace("_", " ")
    return ""
```

`render_terminal` — after the RESULT block (before the per-issue loop):

```python
    if _static_visible(result):
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
```

`render_json` — build the document, then attach the static payload when present:

```python
    data = {"decision": ..., "summary": ..., "issues": [...], "checks": [...]}
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
```

`render_markdown` — after the Issues table, before `## Checks`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_output.py -q`
Expected: PASS (the pre-existing renderer tests assert they are unaffected by an empty `static`)

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/output.py tests/unit/test_output.py
git commit -m "feat: render static-analysis evidence (terminal, JSON, markdown)"
```

## Task 15: `--doctor` static-analysis section

**Files:**
- Create: `src/ai_review/static_analysis/doctor.py`
- Modify: `src/ai_review/doctor.py`
- Test: `tests/unit/test_static_doctor.py`, `tests/unit/test_doctor.py` (extend)

**Interfaces:**
- Consumes: `config.AppConfig`, `registry.REGISTRY`, `AnalysisContext`.
- Produces: `Probe(label, status, note)`, `probe_static_analysis(cfg, repo_root, profile) -> list[Probe]`; `run_doctor` renders them.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_static_doctor.py`:

```python
"""Doctor probes for the static-analysis layer: warn, never error."""
from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import RepoProfile
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.doctor import probe_static_analysis


class StubAnalyzer(StaticAnalyzer):
    name = "stub"
    executable = "stub"

    def build_argv(self, exe, ctx):
        return [exe]

    def parse(self, run, ctx):
        return []


def _cfg(**tools):
    cfg = AppConfig()
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig(enabled=enabled)
                                 for name, enabled in tools.items()}
    return cfg


def test_disabled_layer_is_a_single_warning():
    cfg = _cfg()
    cfg.static_analysis.enabled = False
    probes = probe_static_analysis(cfg, "/repo", RepoProfile())
    assert [p.status for p in probes] == ["warn"]
    assert "disabled" in probes[0].note


def test_missing_binary_is_a_warning_not_an_error(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: None)
    probes = probe_static_analysis(_cfg(stub=True), "/repo", RepoProfile())
    stub = next(p for p in probes if "stub" in p.label)
    assert stub.status == "warn" and stub.note == "not installed"


def test_installed_tool_reports_its_version(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr(StaticAnalyzer, "version", lambda self, ctx: "stub 1.2.3")
    probes = probe_static_analysis(_cfg(stub=True), "/repo", RepoProfile())
    stub = next(p for p in probes if "stub" in p.label)
    assert stub.status == "ok" and stub.note == "stub 1.2.3"


def test_configured_off_tools_are_listed_as_disabled(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "stub", StubAnalyzer)
    probes = probe_static_analysis(_cfg(stub=False), "/repo", RepoProfile())
    stub = next(p for p in probes if "stub" in p.label)
    assert stub.status == "warn" and "disabled" in stub.note


def test_unconfigured_tools_are_summarized_on_one_line(monkeypatch):
    monkeypatch.setitem(registry.REGISTRY, "other", StubAnalyzer)
    probes = probe_static_analysis(_cfg(), "/repo", RepoProfile())
    summary = next(p for p in probes if "not configured" in p.label)
    assert "other" in summary.note and summary.status == "warn"
```

Append to `tests/unit/test_doctor.py`:

```python
def test_doctor_reports_static_analysis_section(repo, llm_unreachable):
    rc, lines = run_doctor(str(repo))
    text = "\n".join(lines)
    assert "Static analysis" in text
    assert rc == 0                      # missing optional tools are warnings only


def test_doctor_static_section_respects_disabled_config(repo, llm_unreachable):
    (repo / ".ai-review.yaml").write_text("static_analysis:\n  enabled: false\n",
                                          encoding="utf-8")
    rc, lines = run_doctor(str(repo))
    assert "Static Analysis — disabled in config" in "\n".join(lines)
    assert rc == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_static_doctor.py tests/unit/test_doctor.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_review.static_analysis.doctor`

- [ ] **Step 3: Implement**

`src/ai_review/static_analysis/doctor.py`:

```python
"""Availability probes for ``--doctor`` (never an error, never a real analysis).

Spec §15/§29: every tool is optional, and a missing one is a warning. Doctor
answers "installed?", not "applicable?": applicability depends on the staged
change set, which doctor does not have.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_review.config import AppConfig
from ai_review.models import RepoProfile
from ai_review.static_analysis.context import AnalysisContext, with_config
from ai_review.static_analysis.registry import REGISTRY


@dataclass(frozen=True)
class Probe:
    """One doctor line: a label, an ``ok``/``warn`` status and a note."""
    label: str
    status: Literal["ok", "warn"]
    note: str = ""


def probe_static_analysis(cfg: AppConfig, repo_root: str,
                          profile: RepoProfile) -> list[Probe]:
    """Probe every configured analyzer: enabled/installed/version."""
    analysis_cfg = cfg.static_analysis
    if not analysis_cfg.enabled:
        return [Probe(label="Static Analysis", status="warn", note="disabled in config")]
    ctx = with_config(AnalysisContext(repo_root=repo_root, changes=[], profile=profile),
                      analysis_cfg)
    probes: list[Probe] = []
    for name in sorted(REGISTRY):
        entry = analysis_cfg.tools.get(name)
        if entry is None:
            continue
        analyzer = REGISTRY[name]()
        if not entry.enabled:
            probes.append(Probe(f"Static analysis: {name}", "warn", "disabled in config"))
            continue
        if not analyzer.is_available(ctx):
            probes.append(Probe(f"Static analysis: {name}", "warn", "not installed"))
            continue
        probes.append(Probe(f"Static analysis: {name}", "ok",
                            analyzer.version(ctx) or "available"))
    unconfigured = sorted(set(REGISTRY) - set(analysis_cfg.tools))
    if unconfigured:
        probes.append(Probe("Static analysis tools not configured", "warn",
                            ", ".join(unconfigured)))
    return probes
```

In `src/ai_review/doctor.py`: add the import, keep `profile` alive outside the detection `try`, and render the probes after the redaction probe:

```python
from ai_review.models import RepoProfile
from ai_review.static_analysis.doctor import probe_static_analysis
```

```python
    profile = RepoProfile()                 # before the try: reused by the probes
    try:
        profile = detect([], root)
        ...
```

```python
    if cfg is not None:
        for probe in probe_static_analysis(cfg, root, profile):
            if probe.status == "ok":
                ok(probe.label + (f" — {probe.note}" if probe.note else ""))
            else:
                warn(probe.label, probe.note)
    else:
        warn("Static Analysis", "skipped (no config)")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_static_doctor.py tests/unit/test_doctor.py tests/unit/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: Manually verify doctor against the real repo**

Run: `.venv/bin/ai-review --doctor`
Expected: the usual probes plus a `Static analysis: <tool> — not installed` line per enabled phase-1 tool, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add src/ai_review/static_analysis/doctor.py src/ai_review/doctor.py \
        tests/unit/test_static_doctor.py tests/unit/test_doctor.py
git commit -m "feat: doctor probe for static-analysis tool availability"
```

## Task 16: Documentation and example config

**Files:**
- Modify: `README.md`, `examples/.ai-review.example.yaml`

**Interfaces:**
- Consumes: everything above.
- Produces: user-facing docs for the new layer, its flags and its config block.

- [ ] **Step 1: Update `examples/.ai-review.example.yaml`**

Append a fully commented block:

```yaml
# Deterministic static analysis: external tools whose findings are handed to the
# AI as EVIDENCE (they never block a commit on their own — the policy engine and
# the offline security/resolution gates decide blocking).
static_analysis:
  enabled: true
  fail_on_error: false          # true: a failed tool becomes a CLI error (exit 2)
  timeout: 120                  # per-tool seconds
  concurrency: 4                # bounded parallel tool execution
  max_findings: 200             # per-tool cap before dedup
  tools:                        # opt-in: a tool runs only when enabled here
    semgrep: { enabled: true }  # needs a repo semgrep config; never --config auto
    phpstan: { enabled: true }
    eslint: { enabled: true }
    ruff: { enabled: true }
    staticcheck: { enabled: true }
```

- [ ] **Step 2: Update `README.md`**

Add to the Commands table:

```text
ai-review --static-only           run offline checks only (static + security + resolution)
ai-review --no-static-analysis    disable the static-analysis layer for this run
```

and a section (before `## Privacy`):

```markdown
## Static analysis

`ai-review` runs the available external analyzers on your staged change set
before the AI review and hands their findings to the model as **evidence**:

- Tools are optional. A missing, slow, failing or unparsable tool is reported
  and skipped; it never fails the review and never blocks a commit.
- Tool findings never block by themselves. Only validated AI findings and the
  offline gates (secrets, unresolved references) reach the policy engine.
- The AI is asked to verify each finding against the source, classify false
  positives explicitly, and reference the finding id it assessed.
- No analyzer downloads rule databases or sends metrics: a run makes no network
  calls of its own, and semgrep is never invoked with `--config auto`.
- Turn the layer off for a run with `--no-static-analysis`, or inspect it
  without an LLM via `--static-only`. `--doctor` lists per-tool availability.
```

- [ ] **Step 3: Verify the suite and the CLI**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ai-review --dry-run && .venv/bin/ai-review --static-only`
Expected: suite PASS; both commands exit 0 (the second prints the static/security/resolution report with no LLM)

- [ ] **Step 4: Commit**

```bash
git add README.md examples/.ai-review.example.yaml
git commit -m "docs: static-analysis layer usage, flags and configuration"
```

## Phase SA-1 exit criteria

- `src/ai_review/static_analysis/` contains `context.py`, `base.py`, `runner.py`, `normalizer.py`, `deduplicator.py`, `registry.py`, `doctor.py`, `__init__.py`, `analyzers/__init__.py` (empty `MODULES`).
- `.venv/bin/python -m pytest` green; the pre-existing tests are unmodified except `tests/unit/test_cli.py`'s `build_pipeline` fake (new keyword) and the additive assertions listed above.
- `.venv/bin/ai-review --dry-run` prints the static step as planned-only; `.venv/bin/ai-review --static-only` reports offline findings with no provider; `.venv/bin/ai-review --doctor` lists per-tool availability and still exits 0.
- Nothing runs a subprocess unless a tool is explicitly enabled *and* registered.

---

# Phase SA-2 — Analyzers, priority 1 (upstream §37)

Five analyzers, one task each. Every analyzer is a small module: identity + gating + `build_argv` + `parse`, plus an explicit severity map. All are tested from checked-in fixtures (`tests/fixtures/static_analysis/`), never from a real tool binary (upstream §32/§33).

Shared helpers added to `normalizer.py` in Task 17:

```python
def relative_path(path: str | None, repo_root: str) -> str:
    """Repo-relative path for a tool's absolute output (never ``../a.py``)."""
    text = str(path or "")
    if text and os.path.isabs(text):
        try:
            return os.path.relpath(text, repo_root)
        except ValueError:                # different drive/mount on Windows
            return text
    return text
```

and to `runner.py`:

```python
def parse_fixture(name: str) -> str:
    """Read ``tests/fixtures/static_analysis/<name>`` (test helper, tiny)."""
```

— no: fixtures belong to the tests. Tests read them with `Path(__file__).parent`-relative paths, e.g.

```python
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "static_analysis"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
```

`tests/unit/test_static_analyzers.py` starts with:

```python
"""Per-analyzer argv + parser tests, driven by checked-in tool fixtures."""
import json
from pathlib import Path

import pytest

from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import ProfileEntry, RepoProfile, StagedChange
from ai_review.static_analysis import registry
from ai_review.static_analysis.context import build_context

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "static_analysis"


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _ctx(tmp_path, files, changes=None, languages=("Python",), frameworks=()):
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    cfg = AppConfig()
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig() for name in registry.REGISTRY}
    profile = RepoProfile(languages=[ProfileEntry(lang, 0.9, []) for lang in languages],
                          frameworks=[ProfileEntry(fw, 0.9, []) for fw in frameworks])
    staged = changes if changes is not None else [
        StagedChange(path=name, status="modified") for name in files]
    return build_context(str(tmp_path), cfg, staged, profile)


def _analyzer(name):
    return registry.REGISTRY[name]()


def _run(analyzer, ctx, stdout="", exit_code=0):
    from ai_review.static_analysis.runner import ToolRun
    return analyzer.parse(ToolRun(argv=["x"], status="run", exit_code=exit_code,
                                  stdout=stdout), ctx)
```

Each analyzer task then follows the same four steps: write the fixture + test (Step 1), watch it fail (Step 2), implement the module and append its name to `analyzers.MODULES` (Step 3), run the tests and commit (Steps 4–5).

## Task 17: Semgrep

**Files:**
- Create: `src/ai_review/static_analysis/analyzers/semgrep.py`
- Modify: `src/ai_review/static_analysis/normalizer.py` (`relative_path`), `.../analyzers/__init__.py` (`MODULES`)
- Create: `tests/fixtures/static_analysis/semgrep.json`
- Test: `tests/unit/test_static_analyzers.py` (semgrep section)

**Fixture** `tests/fixtures/static_analysis/semgrep.json`:

```json
{
  "results": [
    {
      "check_id": "php.lang.security.sql-injection.tainted-sql-string",
      "path": "app/Models/User.php",
      "start": {"line": 42, "col": 10},
      "end": {"line": 42, "col": 50},
      "extra": {
        "message": "User input reaches a raw SQL string.",
        "severity": "ERROR",
        "fingerprint": "07f1c1f1f0e1d0c0b0a090807060504030201000",
        "metadata": {"category": "security", "confidence": "HIGH", "cwe": ["CWE-89: SQL Injection"]}
      }
    },
    {
      "check_id": "python.lang.correctness.useless-eqeq",
      "path": "app/util.py",
      "start": {"line": 7},
      "extra": {"message": "Comparison is always false.", "severity": "WARNING",
                "fingerprint": "000102030405060708090a0b0c0d0e0f10111213",
                "metadata": {"category": "correctness", "confidence": "MEDIUM"}}
    }
  ],
  "errors": []
}
```

**Test:**

```python
SEMGREP = _fixture("semgrep.json")


def test_semgrep_requires_a_repo_rule_config(tmp_path):
    ctx = _ctx(tmp_path, {"app/util.py": "x = 1\n"})
    assert _analyzer("semgrep").build_argv("/usr/bin/semgrep", ctx) == []


def test_semgrep_argv_pins_config_targets_and_privacy_flags(tmp_path):
    ctx = _ctx(tmp_path, {"semgrep.yml": "rules: []\n", "app/util.py": "x = 1\n"})
    argv = _analyzer("semgrep").build_argv("/usr/bin/semgrep", ctx)
    assert argv[:6] == ["/usr/bin/semgrep", "scan", "--json", "--quiet",
                        "--disable-version-check", "--metrics=off"]
    assert "--config" in argv and "semgrep.yml" in argv
    assert "--config=auto" not in argv and "auto" not in argv
    assert argv[-1] == "app/util.py"


def test_semgrep_env_disables_telemetry():
    assert _analyzer("semgrep").env == {"SEMGREP_SEND_METRICS": "off",
                                       "SEMGREP_ENABLE_VERSION_CHECK": "0"}


def test_semgrep_parses_severity_category_cwe_and_fingerprint(tmp_path):
    ctx = _ctx(tmp_path, {"semgrep.yml": "rules: []\n", "app/Models/User.php": "<?php\n"})
    findings = _run(_analyzer("semgrep"), ctx, stdout=SEMGREP)
    assert [f.rule_id for f in findings] == [
        "php.lang.security.sql-injection.tainted-sql-string",
        "python.lang.correctness.useless-eqeq"]
    first = findings[0]
    assert (first.severity, first.original_severity) == ("HIGH", "ERROR")
    assert first.category == "SECURITY"
    assert first.file == "app/Models/User.php" and first.line == 42
    assert first.confidence == 0.9                     # metadata confidence HIGH
    assert first.fingerprint.startswith("07f1c1f1")
    assert "CWE-89" in first.evidence
    assert findings[1].severity == "MEDIUM"            # WARNING mapping


def test_semgrep_malformed_output_fails_the_tool_not_the_review(tmp_path, monkeypatch):
    from ai_review.static_analysis import runner
    ctx = _ctx(tmp_path, {"semgrep.yml": "rules: []\n", "app/util.py": "x = 1\n"})
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(argv=list(argv), status="run",
                                                          exit_code=0, stdout="not json"))
    result = _analyzer("semgrep").analyze(ctx)
    assert result.status == "failed" and result.findings == []
```

**Implementation** `analyzers/semgrep.py` — `name="semgrep"`, `executable="semgrep"`, `extensions=(".php", ".py", ".js", ".ts", ".go", ".rb", ".java", ".sql")`, `ok_exit_codes=(0, 1)`, `env={"SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0"}`, `SEVERITY_MAP={"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}` (default `"MEDIUM"`), rule-config discovery over `("semgrep.yml", "semgrep.yaml", ".semgrep.yml", ".semgrep.yaml")` plus `*.yml|*.yaml` under `.semgrep/`, `build_argv` returning `[]` when no config exists, and a `parse` that reuses `runner.load_json_payload(run.stdout)` → `results`, mapping `extra.severity` through `SEVERITY_MAP`, `metadata.category` == `"security"` → `SECURITY`, `confidence_from(metadata.confidence)`, `evidence` from `metadata.cwe` (falling back to `rule: <check_id>`), `fingerprint=extra.fingerprint`, and `relative_path(path, ctx.repo_root)`.

## Task 18: PHPStan

**Files:** create `analyzers/phpstan.py`, fixture `phpstan.json`, tests section; append `"phpstan"` to `MODULES`.

**Fixture** (abridged shape):

```json
{
  "totals": {"errors": 1, "file_errors": 2},
  "files": {
    "app/Services/OrderService.php": {
      "messages": [
        {"message": "Parameter #1 $id of method OrderService::find() expects int, string given.",
         "line": 142, "ignorable": true, "identifier": "argument.type"},
        {"message": "Method OrderService::missing() not found.", "line": 150,
         "ignorable": false, "identifier": "method.notFound"}
      ]
    }
  },
  "errors": []
}
```

Tests must pin: `local_paths` resolution (`vendor/bin/phpstan` ahead of `PATH`), argv with `--error-format=json --no-progress --no-interaction`, configured-paths mode when `phpstan.neon` exists (no explicit file args) vs changed-files mode otherwise, `ok_exit_codes == (0, 1)`, identifier→severity mapping, and that `files.<abs-path>` keys are normalized with `relative_path`.

**Implementation notes:** `languages=("PHP",)`, `scope=SCOPE_PROJECT`, `local_paths=("vendor/bin/phpstan", "vendor/bin/phpstan.phar")`, `PHPSTAN_SEVERITY` prefix table —
`HIGH`: `argument.`, `method.nonObject`, `property.nonObject`, `variable.undefined`, `class.notFound`, `function.notFound`, `method.notFound`, `return.type`, `binaryOp.invalid`, `offsetAccess.nonOffsetAccessible`;
`LOW`: `missingType.`, `missingReturn.`, `deadCode.`, `unused*`, `deprecated`;
default `MEDIUM`; identifier absent → `MEDIUM` with `rule_id=None`.

## Task 19: ESLint

**Files:** create `analyzers/eslint.py`, fixture `eslint.json`, tests section; append `"eslint"` to `MODULES`.

**Fixture:**

```json
[
  {
    "filePath": "/repo/src/app.ts",
    "messages": [
      {"ruleId": "security/detect-eval-with-expression", "severity": 2, "line": 12,
       "column": 5, "message": "eval with a variable is dangerous."},
      {"ruleId": "no-unused-vars", "severity": 1, "line": 20, "column": 7,
       "message": "'x' is defined but never used."}
    ],
    "errorCount": 1,
    "warningCount": 1
  }
]
```

Tests pin: `local_paths == ("node_modules/.bin/eslint",)`, argv `[exe, "--format", "json", "--no-color", "--no-error-on-unmatched-pattern", *targets]`, `ok_exit_codes == (0, 1)`, severity 2 → `MEDIUM` / 1 → `LOW` except security rules → `HIGH` + `SECURITY` category, absolute `filePath` normalized relative to the repo root, and a fatal parse error message (`ruleId: null`, `severity: 2`) still becoming a finding rather than being dropped.

## Task 20: Ruff

**Files:** create `analyzers/ruff.py`, fixture `ruff.json`, tests section; append `"ruff"` to `MODULES`.

**Fixture:**

```json
[
  {"code": "S105", "filename": "/repo/app/config.py",
   "location": {"row": 3, "column": 1}, "end_location": {"row": 3, "column": 20},
   "message": "Possible hardcoded password assigned to: \"API_KEY\"",
   "noqa_row": 3, "url": "https://docs.astral.sh/ruff/rules/hardcoded-password-string"},
  {"code": "F821", "filename": "/repo/app/main.py",
   "location": {"row": 9, "column": 12}, "message": "Undefined name `fetch_user`"}
]
```

Tests pin: argv `[exe, "check", "--output-format", "json", "--no-cache", "--quiet", *targets]` for `.py`/`.pyi` targets, `ok_exit_codes == (0, 1)`, `S105` → `HIGH` + `SECURITY`, `F821` → `HIGH` and category `BUG`, an unknown code falling back to `LOW`, and empty stdout (clean run) yielding no findings.

## Task 21: staticcheck

**Files:** create `analyzers/staticcheck.py`, fixture `staticcheck.ndjson`, tests section; append `"staticcheck"` to `MODULES`.

**Fixture** (one JSON object per line — the NDJSON shape `load_json_payload` must tolerate):

```json
{"code":"SA1000","severity":"error","message":"invalid regular expression","location":{"file":"/repo/pkg/parse.go","line":31,"column":9},"end":{"file":"/repo/pkg/parse.go","line":31,"column":40}}
{"code":"ST1005","severity":"warning","message":"error strings should not be capitalized","location":{"file":"/repo/pkg/parse.go","line":44,"column":6}}
```

Tests pin: `languages == ("Go",)`, `scope == "project"`, `build_argv` empty without `go.mod` and `[exe, "-f", "json", "./..."]` with it, `ok_exit_codes == (0, 1)`, `error` → `HIGH` / `warning` → `MEDIUM`, `ST1` → `MAINTAINABILITY`, and NDJSON parsing (both lines become findings).

## Phase SA-2 exit criteria

- `analyzers.MODULES == ("semgrep", "phpstan", "eslint", "ruff", "staticcheck")` and `registry.REGISTRY` matches.
- Fixture-driven parser tests green; no test needs a real tool binary, a network call or an LLM (upstream §32).
- `.venv/bin/ai-review --doctor` and `.venv/bin/ai-review --dry-run` unchanged in behaviour on this repo (no tools installed → warnings only).
- Full suite green; one commit per analyzer.

---

# Phase SA-3 — Analyzers, priority 2 (upstream §37)

Tasks (detailed at execution, same four-step pattern as SA-2 — fixture, failing test, module, commit):

- **Larastan** (`larastan`): PHP + `Laravel` framework gate, `local_paths=("vendor/bin/larastan",)`, `argv=[exe, "analyse", "--error-format=json", "--no-progress"]`, PHPStan-shaped JSON parser reused via a shared helper `parse_phpstan_report`.
- **TypeScript** (`typescript`): `languages=("TypeScript",)`, `local_paths=("node_modules/.bin/tsc",)`, `argv=[exe, "--noEmit", "--pretty", "false"]` (respects `tsconfig.json`; never runs application code), text parser for `file(line,col): error TSxxxx: message`, `ok_exit_codes=(0, 1, 2)` because `tsc` exits 2 on type errors; severity `HIGH` for `TS1xxx` type errors, `LOW` for `TS6xxx` config notes.
- **go vet** (`govet`): `languages=("Go",)`, `scope="project"`, `argv=["go", "vet", "./..."]`, stderr text parser (`file:line:col: message`), `ok_exit_codes=(0, 1)`, severity `MEDIUM`.
- **SQLFluff** (`sqlfluff`): `languages=("SQL",)`, `extensions=(".sql",)`, `argv=[exe, "lint", "--format", "json", *targets]`, JSON `[{"filepath":..., "violations":[{"code","line_no","description"}]}]`, severity by code prefix (`PRS`/`L` → `MEDIUM`, `LT`/`CP` → `LOW`).
- **Hadolint** (`hadolint`): Dockerfile paths only (staged files whose basename is `Dockerfile`, `Dockerfile.*`, `*.dockerfile`), `argv=[exe, "--format", "json", *targets]`, JSON `[{"file","line","code","level","message"}]`, `warning` → `MEDIUM`, `error` → `HIGH`, `info`/`style` → `LOW`.
- **Trivy** (`trivy`): `enabled: false` in the shipped defaults, `argv=[exe, "config", "--format", "json", "--scanners", "misconfig", "--skip-db-update", "."]` — `--skip-db-update` is mandatory so a pre-commit never downloads a database (upstream §12); JSON `Results[].Misconfigurations[]` with `ID/AVDID`, `Severity`, `Title/Description`, `CauseMetadata.StartLine`.

Exit: each analyzer's fixtures/tests green; the suite still needs no network and no external binary.

---

# Phase SA-4 — Analyzers, priority 3 (upstream §37)

Tasks (detailed at execution; all default `enabled: false` in shipped defaults and each requires explicit configuration):

- **CodeQL**: CI-only. Requires `static_analysis.tools.codeql.config` (database path or `--database` argument) — **not implemented in SA-4** unless the config gains a per-tool `args` field; until then the analyzer exists, reports `unavailable`/`skipped` with a clear reason, and never downloads a database.
- **SonarQube**: CI-only. Requires an explicitly configured server URL + token; `unavailable` with a clear reason otherwise. Never a network call from the pre-commit path by default.
- **Checkov** (`checkov -d . -o json --compact`), **TFLint** (`tflint --format json`), **kubeconform** (`kubeconform -output json <manifests>`), **kube-linter** (`kube-linter lint --format json <manifests>`) — all infra analyzers gated on the detected `Terraform`/`Kubernetes` infrastructure entries from `detector.py`.

Exit: infra analyzers parse their fixtures, remain opt-in, and cannot reach the network without explicit configuration.

---

# Cross-phase constraints

- **Ordering is fixed**: SA-1 (framework, green) → SA-2 (priority-1 analyzers) → SA-3 → SA-4. Each analyzer registers in `analyzers.MODULES`; nothing else in the pipeline changes when one is added.
- **No analyzer gets a blocking path.** Any future request to "let static findings block" is a design change (design D1, spec §25/§35) and must go through the policy engine explicitly, not through `hard_block`.
- **Config schema is append-only**: new per-tool keys get pydantic defaults so older configs keep validating.
- **Every phase ends with the full suite green** (`.venv/bin/python -m pytest -q`) and `--doctor`/`--dry-run` smoke checks, with one commit per task so a regression is bisectable.

# Final verification (after SA-4)

```bash
.venv/bin/python -m pytest -q                 # all green, fully offline
.venv/bin/ai-review --doctor                  # per-tool availability, exit 0 or 1 only
.venv/bin/ai-review --dry-run                 # planned-only static step, no external calls
.venv/bin/ai-review --static-only             # offline report, provider never constructed
.venv/bin/ai-review --no-static-analysis      # prompt byte-identical to the pre-feature prompt
```

Report (upstream §40): architecture changes, files added, files modified, analyzers implemented, configuration changes, CLI changes, tests added, test results, known limitations (no SARIF yet, per-tool `args` not configurable, CodeQL/SonarQube require external setup), and the next recommended phase.

### Why this plan is split into two files

Part 1 exceeded the size the workspace file tools can reliably patch (~60 KB), so the remainder lives in this file. Part 1 remains the reference for the goal, constraints, resolutions RD-1…RD-11 and Tasks 1–8; the split is purely mechanical and does not change the task order or numbering.
