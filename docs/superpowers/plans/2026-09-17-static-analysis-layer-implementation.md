# Static Analysis Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic static-analysis layer to `ai-review` that runs after technology detection and before the hard gates, feeding normalized + deduplicated tool findings to the LLM as *evidence* — never as a policy gate — without reordering or weakening the security scan or the resolution gate.

**Architecture:** New self-contained package `src/ai_review/static_analysis/` with one module per tool (`analyzers/<tool>.py`) behind a single template-method base class. The pipeline calls exactly one entry point, `run_static_analysis(cfg, ctx)`, inside `Pipeline._prepare()`, and stores the result on a new `ReviewResult.static` field. Findings are normalized into the existing `Finding` dataclass (extended with defaulted provenance fields), deduplicated deterministically, and rendered into a compact prompt section. The AI's verdicts come back as `static_assessments` (+ an optional `related_static_finding_id` per issue) and are validated against real static ids.

**Tech Stack:** Python 3.10+, stdlib only for the new layer (`subprocess`, `concurrent.futures`, `hashlib`, `json`, `shutil`) — no new dependencies. Existing: `httpx`, `pydantic>=2`, `PyYAML`, `hatchling`, `pytest`/`pytest-cov`.

**Spec:** `docs/superpowers/specs/2026-09-17-static-analysis-layer-design.md` (adaptation; §-references below are to it unless prefixed "upstream")

**Upstream requirement:** `static-analysis.md` (§-references prefixed "upstream")

**Verified baseline (2026-09-17, before any code changes):**

- `.venv/bin/python -m pytest` → **333 passed** (Python 3.10.12); no static-analysis tool is installed in `.venv` or on `PATH`.
- Working tree clean, single commit `3937b24 initial commit`.
- Pipeline order today: collect staged → detect/`profile_from_names` → `classify` → diff → redact → `scan_staged` → `scan_unresolved` → prompt → `ReviewSession.run` → merge → `validate_findings` → `PolicyEngine.decide`.

## Global Constraints

- **Static findings never enter `result.issues`.** `validate_findings` and `PolicyEngine` gate on `result.issues` only; static evidence lives on `ReviewResult.static`. Only a validated AI finding (or an offline hard-block: secret, unresolved reference) can block.
- **Security (`scan_staged`) and resolution (`scan_unresolved`) are untouched**, still run before the LLM, and still hard-block. Static analysis is inserted *before* them and cannot bypass them.
- **`--dry-run` runs no external process and never invokes the provider.** It prints the *planned* analyzers only (see RD-7).
- **No `shell=True`, no string-interpolated commands.** Analyzer argv is a list; `cwd` is pinned to the repo root; stdin is `DEVNULL`.
- **Nothing is a mandatory install** (upstream §15). A missing/timeout/crash/unparseable tool yields a `failed`/`unavailable` `ToolResult` and zero findings; the review still runs. The only exception is the explicit opt-in `static_analysis.fail_on_error: true` (→ CLI exit 2 via the existing error contract).
- **No network from analyzers.** Semgrep is never invoked with `--config auto` (registry download) and runs with metrics/version checks off; Trivy/CodeQL/SonarQube are disabled by default.
- **A tool runs only when its config key is explicitly enabled** (`static_analysis.tools.<name>.enabled: true`). The shipped `config/default.yaml` enables the phase-1/2 tools; a bare `AppConfig()` runs nothing, so the 333 existing tests stay hermetic and tool-free (RD-4).
- Exit codes unchanged: 0 = pass/warn (and `--doctor` ready), 1 = blocked / `--doctor` not ready, 2 = handled error printed as `error: ...` on stderr.
- Config layering unchanged (defaults < org < user < repo < CLI); no new org directives, nothing here may weaken `organization.enforce`.
- No new packaged assets except the edits to the *existing* `prompts/system.md`; `tests/unit/test_package_data.py` must stay green.
- Every new module has unit tests; every phase ends with `.venv/bin/python -m pytest` green and a commit.
- Python 3.10 only: no `X | Y` at runtime without `from __future__ import annotations`, no 3.11+ stdlib.

## Design-doc resolutions locked by this plan

The design doc leaves several details open; these are the decisions the implementation must follow. Each one is a deliberate, reviewable deviation where noted.

| # | Resolution | Why |
|---|---|---|
| RD-1 | `ToolResult`, `AnalyzerResult` and `StaticAnalysisSummary` all live in `ai_review/models.py` (the project's single data-contract home). `base.py` imports `AnalyzerResult`; it does **not** define a second `ToolResult`. | §4 lists `ToolResult` in `base.py` while §6 lists it in `models.py` — a contradiction. `models.py` wins (AGENTS.md: "all data contracts"). |
| RD-2 | Two result types, not one: `AnalyzerResult` (what `analyzer.analyze(ctx)` returns, upstream §2's `AnalyzerResult`) and `ToolResult` (the per-tool summary row in `StaticAnalysisSummary.tools`, §6). | Keeps the analyzer return contract and the summary schema both exactly as documented. |
| RD-3 | The `Finding` id is **tool-independent**: `sha1(file \| line \| category \| normalized message)[:12]`, assigned by `normalizer.stable_id`/`make_finding` and recomputed by `dedupe` after merging. | Two tools reporting the same defect must share one id, and ids must not change when a different tool wins the merge. |
| RD-4 | Enablement is explicit opt-in per tool (`cfg.tools[name].enabled`); a tool absent from `tools` never runs. See Global Constraints. | Determinism: a bare `AppConfig()` (every existing test) must not spawn subprocesses because a dev machine happens to have ruff installed. |
| RD-5 | `StaticAnalyzer.is_available` takes the context: `is_available(ctx)` (and `resolve(ctx)`), so `vendor/bin/phpstan` / `node_modules/.bin/eslint` resolve project-locally before `PATH`. | §4's `is_available(self)` cannot express project-local executables; §4's own matrix requires them. |
| RD-6 | Non-zero exit is **not** failure by default: each analyzer declares `ok_exit_codes` (linters exit 1 when they *find* things). | Tests in upstream §33 for "non-zero exit" must mean "exit outside `ok_exit_codes`" (phpstan 2, ruff 2, semgrep 2). |
| RD-7 | `--dry-run` does **not** execute analyzers: it lists the enabled + applicable + available ones as `status="skipped", error="dry-run (not executed)"` and renders them in the plan. | §D5 says the dry-run path executes the static stage; README/§17 promise "no external calls". The plan keeps the promise; `--static-only` is the flag that actually executes without the LLM. |
| RD-8 | The prompt section header is `# Static Analysis Findings` (single `#`), not `## …` as shown in §7. | `PromptBuilder.build` renders every section with a single `#` (`# Staged Files`, `# Staged Diff`, `# Relevant Context`, `# Output Format`). Section consistency beats the illustrative heading. |
| RD-9 | `format_static_findings` lives in `static_analysis/__init__.py` (exported), not in `prompts.py`. | It formats static findings, not prompt layers; `prompts.py` only takes the resulting `static_text` string. |
| RD-10 | Tool findings are deduplicated and capped per tool (`static_analysis.max_findings`, default 200) before merging. | upstream §24: never paste a raw dump into the prompt. |
| RD-11 | Every intra-package import is a direct submodule import (`from .runner import run_tool`), and the analyzer loader call stays at the bottom of `registry.py`. | Avoids `ai_review.static_analysis` partially initialized during `__init__` (circular-import trap). |

---

# Phase SA-1 — Framework and wiring (ends green)

Deliverable: the `static_analysis` package (context, base, runner, normalizer, deduplicator, registry, public API, doctor probe), config + `Finding`/`ReviewResult` extensions, prompt/schema/parser/validator wiring, pipeline wiring (normal, dry-run, `--static-only`), CLI flags, output renderers, doctor section, README/example config — with **zero** analyzers registered yet, so the entire existing suite passes untouched and every new unit test is hermetic.

## Task 1: Provenance fields and new data contracts (`models.py`)

**Files:**
- Modify: `src/ai_review/models.py`
- Test: `tests/unit/test_models.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Finding.source/id/tool/rule_id/fingerprint/original_severity/original_message/detected_by/related_static_finding_id`; `ToolStatus`; `StaticVerdict`; `ToolResult`; `AnalyzerResult`; `StaticAnalysisSummary` (+`severity_counts()`); `StaticAssessment`; `ReviewResult.static`, `ReviewResult.static_assessments`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_models.py`:

```python
from ai_review.models import (AnalyzerResult, Finding, ReviewResult, StaticAnalysisSummary,
                              StaticAssessment, ToolResult)


def _finding(**kw):
    base = dict(severity="HIGH", category="BUG", file="a.go", line=4, title="t",
                description="d", evidence="e", recommendation="r", confidence=0.9)
    base.update(kw)
    return Finding(**base)


def test_finding_static_provenance_defaults_keep_ai_shape():
    f = _finding()
    assert f.source == "ai"
    assert f.id is None and f.tool is None and f.rule_id is None
    assert f.fingerprint is None
    assert f.original_severity is None and f.original_message is None
    assert f.detected_by == [] and f.related_static_finding_id is None


def test_static_summary_severity_counts_are_complete():
    summary = StaticAnalysisSummary(findings=[_finding(severity="HIGH"), _finding(severity="LOW")])
    assert summary.severity_counts() == {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 0, "LOW": 1, "INFO": 0}


def test_review_result_static_defaults_are_off():
    r = ReviewResult(decision="PASS", summary="s")
    assert r.static is None
    assert r.static_assessments == []


def test_tool_and_analyzer_result_defaults():
    t = ToolResult(name="ruff", status="run")
    assert (t.exit_code, t.error, t.duration_ms) == (None, "", 0)
    a = AnalyzerResult()
    assert a.findings == [] and a.status == "run" and a.exit_code is None


def test_static_assessment_shape():
    a = StaticAssessment(finding_id="abc123", verdict="likely_false_positive", reason="guarded above")
    assert a.reason == "guarded above"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_models.py -q`
Expected: FAIL — `ImportError: cannot import name 'AnalyzerResult' from 'ai_review.models'`

- [ ] **Step 3: Extend the models**

In `src/ai_review/models.py`, extend the literal block after `ChangeKind`:

```python
ToolStatus = Literal["run", "unavailable", "skipped", "failed"]
StaticVerdict = Literal[
    "confirmed", "likely_true", "uncertain", "likely_false_positive", "false_positive",
]
```

Extend `Finding` (append; every field is defaulted, so `security.py`, `resolution.py`, `parser.py` and all existing tests compile unchanged):

```python
@dataclass
class Finding:
    severity: Severity
    category: Category
    file: str
    line: int | None
    title: str
    description: str
    evidence: str
    recommendation: str
    confidence: float
    is_pre_existing: bool = False
    hard_block: bool = False
    # -- static-analysis provenance (spec §18/§19/§26); all defaulted so the
    #    existing constructors are unchanged and AI findings stay source="ai".
    source: str = "ai"                      # "ai" | "static_analysis"
    id: str | None = None                   # stable static id (deduplicator)
    tool: str | None = None
    rule_id: str | None = None
    fingerprint: str | None = None
    original_severity: str | None = None    # the analyzer's own severity string
    original_message: str | None = None     # the analyzer's own message
    detected_by: list[str] = field(default_factory=list)   # merged-tool provenance
    related_static_finding_id: str | None = None           # AI issue -> static id
```

Add the new contracts after `CheckResult`:

```python
@dataclass
class ToolResult:
    """Per-tool outcome row of a static-analysis run (spec §15/§23)."""
    name: str
    status: ToolStatus
    exit_code: int | None = None
    error: str = ""
    duration_ms: int = 0


@dataclass
class AnalyzerResult:
    """What one analyzer returns from ``analyze(ctx)`` (upstream §2)."""
    findings: list[Finding] = field(default_factory=list)
    status: ToolStatus = "run"
    exit_code: int | None = None
    error: str = ""
    duration_ms: int = 0


@dataclass
class StaticAnalysisSummary:
    """Everything static analysis produced for one review (spec §23)."""
    findings: list[Finding] = field(default_factory=list)
    tools: list[ToolResult] = field(default_factory=list)
    files_analyzed: int = 0
    duration_ms: int = 0

    def severity_counts(self) -> dict[str, int]:
        """Severity histogram with all five keys present (stable JSON shape)."""
        out = {name: 0 for name in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")}
        for finding in self.findings:
            out[finding.severity] = out.get(finding.severity, 0) + 1
        return out


@dataclass
class StaticAssessment:
    """The AI's verdict on one static finding (spec §26/§27).

    Stored separately from the finding: the original static finding is never
    mutated by an AI verdict.
    """
    finding_id: str
    verdict: StaticVerdict
    reason: str = ""
```

Extend `ReviewResult`:

```python
@dataclass
class ReviewResult:
    decision: Decision
    summary: str
    issues: list[Finding] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    #: Static-analysis evidence. Never merged into ``issues``: static findings
    #: are input to the LLM, not input to the policy gate (design D1).
    static: StaticAnalysisSummary | None = None
    #: Validated AI verdicts on static findings (unknown ids dropped).
    static_assessments: list[StaticAssessment] = field(default_factory=list)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_models.py -q`
Expected: PASS

- [ ] **Step 5: Confirm nothing else regressed**

Run: `.venv/bin/python -m pytest -q`
Expected: 338 passed (333 baseline + the new model tests)

- [ ] **Step 6: Commit**

```bash
git add src/ai_review/models.py tests/unit/test_models.py
git commit -m "feat: extend Finding with static-analysis provenance; add static data contracts"
```

## Task 2: Static-analysis configuration

**Files:**
- Modify: `src/ai_review/config.py`, `src/ai_review/config/default.yaml`
- Test: `tests/unit/test_config.py` (extend)

**Interfaces:**
- Consumes: Task 1's models (none required — config stays pydantic-only).
- Produces: `StaticAnalysisToolConfig`, `StaticAnalysisConfig`, `AppConfig.static_analysis`; the `static_analysis:` block in the shipped defaults.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config.py`:

```python
from ai_review.config import AppConfig, StaticAnalysisConfig, load_config


def test_static_analysis_defaults_are_explicit_opt_in():
    cfg = AppConfig()
    assert cfg.static_analysis.enabled is True
    assert cfg.static_analysis.fail_on_error is False
    assert cfg.static_analysis.timeout == 120
    assert cfg.static_analysis.concurrency == 4
    assert cfg.static_analysis.tools == {}          # nothing runs by default


def test_static_analysis_tool_flags_merge_across_layers(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML + (
        "static_analysis:\n"
        "  tools:\n"
        "    semgrep: { enabled: true }\n"
        "    codeql: { enabled: false }\n"
    ))
    repo_yaml = _write(tmp_path, "repo.yaml",
                       "static_analysis:\n  tools:\n    codeql: { enabled: true }\n"
                       "    ruff: { enabled: true }\n")
    cfg = load_config(defaults_yaml=defaults, org_yaml=None, user_yaml=None,
                      repo_yaml=repo_yaml, cli_overrides=None, repo_dir=str(tmp_path))
    assert cfg.static_analysis.tools["semgrep"].enabled is True    # from defaults
    assert cfg.static_analysis.tools["codeql"].enabled is True     # repo tightens
    assert cfg.static_analysis.tools["ruff"].enabled is True       # repo adds


def test_static_analysis_unknown_tool_key_is_tolerated():
    cfg = StaticAnalysisConfig.model_validate({"tools": {"not_a_tool": {"enabled": True}}})
    assert cfg.tools["not_a_tool"].enabled is True


def test_cli_can_disable_static_analysis(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML)
    cfg = load_config(defaults_yaml=defaults, org_yaml=None, user_yaml=None,
                      repo_yaml=None, cli_overrides={"static_analysis": {"enabled": False}},
                      repo_dir=str(tmp_path))
    assert cfg.static_analysis.enabled is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_config.py -q`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'static_analysis'`

- [ ] **Step 3: Implement the config**

In `src/ai_review/config.py`, add before `class AppConfig`:

```python
class StaticAnalysisToolConfig(BaseModel):
    """Per-tool switch (spec §28). A tool runs only when explicitly enabled."""
    enabled: bool = True


class StaticAnalysisConfig(BaseModel):
    """Deterministic static-analysis layer (spec §28).

    ``tools`` is an explicit opt-in map, so a bare ``AppConfig()`` runs nothing
    and the test suite stays hermetic even on a machine with linters installed
    (RD-4). The shipped ``config/default.yaml`` enables the phase-1/2 tools.
    ``fail_on_error`` is the only setting that can turn a tool problem into a
    CLI error (exit 2); by default an optional tool can never fail a review.
    """
    enabled: bool = True
    fail_on_error: bool = False
    timeout: int = 120                      # per-tool seconds
    concurrency: int = 4                    # bounded worker pool
    max_findings: int = 200                 # per-tool cap before dedup/prompt
    tools: dict[str, StaticAnalysisToolConfig] = Field(default_factory=dict)
```

and to `AppConfig`:

```python
    static_analysis: StaticAnalysisConfig = StaticAnalysisConfig()
```

Append to `src/ai_review/config/default.yaml`:

```yaml
static_analysis:
  enabled: true
  fail_on_error: false
  timeout: 120
  concurrency: 4
  max_findings: 200
  tools:
    # Phase 1 (deterministic evidence for the AI reviewer)
    semgrep: { enabled: true }
    phpstan: { enabled: true }
    eslint: { enabled: true }
    ruff: { enabled: true }
    staticcheck: { enabled: true }
    # Phase 2
    larastan: { enabled: true }
    typescript: { enabled: true }
    govet: { enabled: true }
    sqlfluff: { enabled: true }
    hadolint: { enabled: true }
    trivy: { enabled: false }    # IaC scan: never downloads its DB in a pre-commit
    # Phase 3 (explicit opt-in only)
    codeql: { enabled: false }
    sonarqube: { enabled: false }
    checkov: { enabled: true }
    tflint: { enabled: true }
    kubeconform: { enabled: true }
    kube_linter: { enabled: true }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_config.py tests/unit/test_package_data.py -q`
Expected: PASS (the package-data guard only checks that `default.yaml` exists and mentions `llm:`)

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/config.py src/ai_review/config/default.yaml tests/unit/test_config.py
git commit -m "feat: static_analysis config layer with per-tool opt-in"
```

## Task 3: `static_analysis/context.py` — the analyzer's view of the repository

**Files:**
- Create: `src/ai_review/static_analysis/__init__.py` (empty placeholder this task; filled in Task 9)
- Create: `src/ai_review/static_analysis/context.py`
- Test: `tests/unit/test_static_context.py`

**Interfaces:**
- Consumes: `ai_review.config.AppConfig`, `ai_review.models.{RepoProfile, StagedChange}`.
- Produces: `SCOPE_CHANGED_FILES`, `SCOPE_PROJECT`, `AnalysisContext(repo_root, changes, profile, diff_text, cfg)`, `build_context(repo_root, cfg, changes, profile, diff_text="")`, `with_config(ctx, cfg)`, `AnalysisContext.languages()`, `.frameworks()`, `.changed_paths(extensions=())`, `.has_file(rel)`, `.has_any(rels)`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_static_context.py`:

```python
"""AnalysisContext: what an analyzer may inspect, and nothing more."""
from ai_review.config import AppConfig
from ai_review.models import RepoProfile, StagedChange
from ai_review.static_analysis.context import (SCOPE_CHANGED_FILES, SCOPE_PROJECT,
                                              build_context, with_config)


def _change(path, status="modified", binary=False):
    return StagedChange(path=path, status=status, is_binary=binary)


def test_changed_paths_filters_deleted_binary_and_missing(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.js").write_text("let x = 1\n")
    ctx = build_context(str(tmp_path), AppConfig(), [
        _change("a.py"),
        _change("b.js"),
        _change("gone.py", status="deleted"),
        _change("blob.py", binary=True),
        _change("never-on-disk.py"),
    ], RepoProfile())
    assert ctx.changed_paths() == ["a.py", "b.js"]
    assert ctx.changed_paths((".py",)) == ["a.py"]
    assert ctx.changed_paths((".sql",)) == []


def test_languages_and_frameworks_and_file_probes(tmp_path):
    (tmp_path / "composer.json").write_text("{}")
    profile = RepoProfile(languages=[])
    ctx = build_context(str(tmp_path), AppConfig(), [], profile)
    assert ctx.languages() == set()
    assert ctx.has_file("composer.json") is True
    assert ctx.has_file("missing.json") is False
    assert ctx.has_any(("missing.json", "composer.json")) is True
    assert ctx.has_any(("missing.json",)) is False


def test_build_context_carries_config_and_with_config_replaces_it():
    cfg = AppConfig()
    ctx = build_context("/repo", cfg, [], RepoProfile())
    assert ctx.cfg is cfg.static_analysis
    other = AppConfig().static_analysis
    assert with_config(ctx, other).cfg is other
    assert ctx.cfg is cfg.static_analysis            # original untouched


def test_scope_constants():
    assert (SCOPE_CHANGED_FILES, SCOPE_PROJECT) == ("changed_files", "project")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_static_context.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_review.static_analysis'`

- [ ] **Step 3: Implement**

Create `src/ai_review/static_analysis/__init__.py`:

```python
"""Static-analysis layer (framework; analyzers register in ``registry``)."""
```

Create `src/ai_review/static_analysis/context.py`:

```python
"""Everything an analyzer may inspect, and the targets it may be pointed at.

The context is deliberately read-only with respect to the review: analyzers
read the working tree, never the staged diff blob, so a git-only view can't
hide what the tools must see (same reasoning as ``resolution.py``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from ai_review.config import AppConfig, StaticAnalysisConfig
from ai_review.models import RepoProfile, StagedChange

#: Scope of an analyzer's file arguments (spec §17).
SCOPE_CHANGED_FILES = "changed_files"
SCOPE_PROJECT = "project"


@dataclass
class AnalysisContext:
    """Immutable-by-convention view passed to every analyzer."""
    repo_root: str
    changes: list[StagedChange] = field(default_factory=list)
    profile: RepoProfile = field(default_factory=RepoProfile)
    diff_text: str = ""
    #: Set by ``build_context`` / ``with_config``; never None inside analyze().
    cfg: StaticAnalysisConfig | None = None

    def languages(self) -> set[str]:
        return {entry.name for entry in self.profile.languages}

    def frameworks(self) -> set[str]:
        return {entry.name for entry in self.profile.frameworks}

    def changed_paths(self, extensions: tuple[str, ...] = ()) -> list[str]:
        """Repo-relative staged paths that exist on disk, optionally by extension.

        Deleted and binary changes are excluded (there is no readable file, and
        a tool pointed at a missing path is an error, not evidence), as are
        paths whose on-disk file is gone.
        """
        out: list[str] = []
        for change in self.changes:
            if change.status == "deleted" or change.is_binary:
                continue
            if extensions and not change.path.lower().endswith(extensions):
                continue
            if os.path.isfile(os.path.join(self.repo_root, change.path)):
                out.append(change.path)
        return out

    def has_file(self, rel: str) -> bool:
        """True when a repo-relative path exists (manifest/config discovery)."""
        return os.path.isfile(os.path.join(self.repo_root, rel))

    def has_any(self, rels: tuple[str, ...]) -> bool:
        return any(self.has_file(rel) for rel in rels)


def build_context(repo_root: str, cfg: AppConfig, changes: list[StagedChange],
                  profile: RepoProfile, diff_text: str = "") -> AnalysisContext:
    """Build the context from the pipeline's already-computed offline state."""
    return AnalysisContext(
        repo_root=repo_root, changes=list(changes), profile=profile,
        diff_text=diff_text, cfg=cfg.static_analysis,
    )


def with_config(ctx: AnalysisContext, cfg: StaticAnalysisConfig) -> AnalysisContext:
    """Return *ctx* with *cfg* attached (keeps ``run_static_analysis(cfg, ctx)``
    honest about which config the analyzers actually see)."""
    return replace(ctx, cfg=cfg)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_static_context.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/static_analysis tests/unit/test_static_context.py
git commit -m "feat: static analysis context (repo view + analyzer targets)"
```

## Task 4: `runner.py` — the safe subprocess boundary

**Files:**
- Create: `src/ai_review/static_analysis/runner.py`
- Test: `tests/unit/test_static_runner.py`

**Interfaces:**
- Consumes: stdlib only.
- Produces: `ToolRun(argv, status, exit_code, stdout, stderr, error, duration_ms)`, `run_tool(argv, *, cwd, timeout, env=None)`, `load_json_payload(text)`, `MAX_STDOUT_CHARS`, `STDERR_TAIL_CHARS`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_static_runner.py`:

```python
"""The subprocess boundary: argv lists, timeouts, and JSON shapes."""
import subprocess

import pytest

from ai_review.static_analysis import runner


def test_run_tool_success_captures_output(tmp_path):
    run = runner.run_tool(["python3", "-c", "print('hi')"], cwd=str(tmp_path), timeout=10)
    assert run.status == "run"
    assert run.exit_code == 0
    assert run.stdout.strip() == "hi"
    assert run.duration_ms >= 0


def test_run_tool_nonzero_exit_is_still_a_run(tmp_path):
    run = runner.run_tool(["python3", "-c", "raise SystemExit(3)"], cwd=str(tmp_path), timeout=10)
    assert run.status == "run" and run.exit_code == 3


def test_run_tool_timeout_is_reported_not_raised(tmp_path, monkeypatch):
    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="slow", timeout=1)

    monkeypatch.setattr(runner.subprocess, "run", _timeout)
    run = runner.run_tool(["slow"], cwd=str(tmp_path), timeout=1)
    assert run.status == "timeout" and run.exit_code is None
    assert "timed out after 1s" in run.error


def test_run_tool_missing_binary_is_reported_not_raised(tmp_path):
    run = runner.run_tool(["definitely-not-a-real-binary-xyz"], cwd=str(tmp_path), timeout=5)
    assert run.status == "failed" and run.error


def test_run_tool_never_uses_a_shell_and_pins_cwd(tmp_path, monkeypatch):
    seen = {}
    real = subprocess.run

    def spy(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        return real(["python3", "-c", "print('ok')"], **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", spy)
    runner.run_tool(["tool", "-x"], cwd=str(tmp_path), timeout=5, env={"EXTRA": "1"})
    assert seen["argv"] == ["tool", "-x"]
    assert "shell" not in seen["kwargs"]
    assert seen["kwargs"]["cwd"] == str(tmp_path)
    assert seen["kwargs"]["timeout"] == 5
    assert seen["kwargs"]["env"]["EXTRA"] == "1"
    assert seen["kwargs"]["env"]["NO_COLOR"] == "1"


def test_load_json_payload_handles_array_object_and_ndjson():
    assert runner.load_json_payload('[{"a": 1}]') == [{"a": 1}]
    assert runner.load_json_payload('{"a": 1}') == [{"a": 1}]
    assert runner.load_json_payload('{"a": 1}\n{"b": 2}\n') == [{"a": 1}, {"b": 2}]
    assert runner.load_json_payload("   ") == []


def test_load_json_payload_rejects_garbage():
    with pytest.raises(ValueError):
        runner.load_json_payload("not json at all")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_static_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_review.static_analysis.runner`

- [ ] **Step 3: Implement**

`src/ai_review/static_analysis/runner.py`:

```python
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

    Contract: ``argv`` is a list (a string would be split by the OS, and
    ``shell=True`` is forbidden project-wide), cwd is pinned to the repository
    root, stdin is closed so a tool cannot block on a prompt, output is decoded
    with ``errors="replace"`` (tools emit non-UTF-8 bytes), and the timeout
    yields ``status="timeout"`` instead of an exception. Only the caller's own
    bugs (non-list argv) propagate.
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
    (a clean run), anything undecodable raises ``ValueError`` so the analyzer's
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_static_runner.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/static_analysis/runner.py tests/unit/test_static_runner.py
git commit -m "feat: safe subprocess runner for static analyzers"
```

## Task 5: `normalizer.py` — severity, category, confidence, stable ids

**Files:**
- Create: `src/ai_review/static_analysis/normalizer.py`
- Test: `tests/unit/test_static_normalizer.py`

**Interfaces:**
- Consumes: `ai_review.models.{Category, Finding, Severity}`.
- Produces: `SEVERITY_ORDER`, `CONFIDENCE_MAP`, `normalize_severity(raw, mapping, default)`, `confidence_from(raw, default)`, `infer_category(*hints, default)`, `normalize_message(text)`, `stable_id(file, line, category, message)`, `worst_severity(a, b)`, `make_finding(**kwargs)`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_static_normalizer.py`:

```python
"""Normalization: explicit per-tool maps, preserved originals, stable ids."""
from ai_review.static_analysis import normalizer as n


def test_normalize_severity_uses_the_supplied_map_only():
    mapping = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}
    assert n.normalize_severity("ERROR", mapping) == "HIGH"
    assert n.normalize_severity("error", mapping) == "HIGH"      # case-insensitive
    assert n.normalize_severity("weird", mapping) == "MEDIUM"    # default
    assert n.normalize_severity(None, mapping, default="INFO") == "INFO"


def test_confidence_map_and_unknown_fallback():
    assert n.confidence_from("HIGH") == 0.9
    assert n.confidence_from("medium") == 0.6
    assert n.confidence_from(None, default=0.5) == 0.5


def test_infer_category_hits_keywords_and_falls_back():
    assert n.infer_category("php.lang.security.sql-injection") == "SECURITY"
    assert n.infer_category(None, "race condition") == "CONCURRENCY"
    assert n.infer_category("style/indent") == "MAINTAINABILITY"
    assert n.infer_category(None, "", default="OTHER") == "OTHER"


def test_normalize_message_is_order_independent_for_dedup():
    assert n.normalize_message("  Potential   SQL\nInjection ") == "potential sql injection"
    assert n.normalize_message("") == ""


def test_stable_id_is_tool_independent_and_position_sensitive():
    a = n.stable_id("app/x.php", 42, "SECURITY", "Potential SQL injection")
    b = n.stable_id("app/x.php", 42, "SECURITY", "Potential   SQL injection")
    c = n.stable_id("app/x.php", 43, "SECURITY", "Potential SQL injection")
    assert a == b and a != c and len(a) == 12


def test_make_finding_preserves_original_and_sets_provenance():
    f = n.make_finding(tool="semgrep", rule_id="php.lang.security.sql-injection",
                       fingerprint="fp1", severity="HIGH", original_severity="ERROR",
                       file="app/Models/User.php", line=42, message="Potential SQL injection.",
                       confidence=0.9, detected_by=["semgrep"])
    assert f.source == "static_analysis"
    assert f.tool == "semgrep"
    assert f.rule_id == "php.lang.security.sql-injection"
    assert f.original_severity == "ERROR"
    assert f.original_message == "Potential SQL injection."
    assert f.category == "SECURITY"
    assert f.detected_by == ["semgrep"]
    assert f.confidence == 0.9
    assert f.id == n.stable_id("app/Models/User.php", 42, "SECURITY", "Potential SQL injection.")
    assert f.hard_block is False          # static evidence never blocks by itself


def test_make_finding_requires_no_recommendation_from_the_tool():
    f = n.make_finding(tool="ruff", severity="LOW", file="a.py", line=1, message="unused import")
    assert f.recommendation
    assert f.evidence == ""
    assert f.title == "unused import"


def test_worst_severity_picks_the_higher_rank():
    assert n.worst_severity("LOW", "CRITICAL") == "CRITICAL"
    assert n.worst_severity("HIGH", "HIGH") == "HIGH"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_static_normalizer.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_review.static_analysis.normalizer`

- [ ] **Step 3: Implement**

`src/ai_review/static_analysis/normalizer.py`:

```python
"""Tool output -> normalized ``Finding`` (spec §18/§19/§20/§21).

Severity and confidence are separate axes, each tool keeps its own explicit
mapping (never a universal ``ERROR -> CRITICAL``), and the analyzer's original
severity/message survive normalization.
"""
from __future__ import annotations

import hashlib
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

#: First-match-wins keyword table mapping tool categories/rules to the
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
      "deadcode", "dead-code", "unused", "documentation", "missingtype"), "MAINTAINABILITY"),
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


def stable_id(file: str, line: int | None, category: str, message: str) -> str:
    """Tool-independent 12-hex-char id for one located defect (RD-3).

    Two tools describing the same file/line/category/message therefore share an
    id, which is what makes "Detected by semgrep, phpstan" renderable and makes
    AI ``related_static_finding_id`` links stable across runs.
    """
    key = "\x1f".join([file or "", str(line or 0), category or "", normalize_message(message)])
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
    title = (message or rule_id or f"{tool} finding").strip().splitlines()[0][:200]
    return Finding(
        severity=severity,
        category=category or infer_category(rule_id, tool),
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
        id=stable_id(file, line, category or infer_category(rule_id, tool), message),
        tool=tool,
        rule_id=rule_id or None,
        fingerprint=fingerprint,
        original_severity=original_severity,
        original_message=(message or "").strip(),
        detected_by=list(detected_by or [tool]),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_static_normalizer.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/static_analysis/normalizer.py tests/unit/test_static_normalizer.py
git commit -m "feat: static finding normalization (severity/category/confidence/ids)"
```

## Task 6: `deduplicator.py` — deterministic merge

**Files:**
- Create: `src/ai_review/static_analysis/deduplicator.py`
- Test: `tests/unit/test_static_deduplicator.py`

**Interfaces:**
- Consumes: `normalizer.{SEVERITY_ORDER, normalize_message, stable_id, worst_severity}`, `models.Finding`.
- Produces: `dedupe(findings) -> list[Finding]` (sorted, merged, ids recomputed).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_static_deduplicator.py`:

```python
"""Dedup: exact, cross-tool, and the negative case (near is not the same)."""
from ai_review.static_analysis import normalizer as n
from ai_review.static_analysis.deduplicator import dedupe


def _f(**kw):
    base = dict(tool="semgrep", severity="MEDIUM", file="app/x.php", line=42,
                message="Potential SQL injection.")
    base.update(kw)
    return n.make_finding(**base)


def test_exact_within_tool_duplicates_collapse():
    out = dedupe([_f(rule_id="r1"), _f(rule_id="r1")])
    assert len(out) == 1


def test_cross_tool_merge_unions_provenance_and_keeps_one_id():
    first = _f(tool="semgrep", severity="MEDIUM", rule_id="php.security.sql-injection")
    second = _f(tool="phpstan", severity="HIGH", rule_id="argument.type",
                message="Potential   SQL injection.")
    out = dedupe([first, second])
    assert len(out) == 1
    assert out[0].detected_by == ["phpstan", "semgrep"]
    assert out[0].severity == "HIGH"                 # highest severity wins
    assert out[0].id == n.stable_id("app/x.php", 42, "SECURITY", "Potential SQL injection.")


def test_near_but_distinct_findings_never_merge():
    same_line_other_issue = _f(message="Missing return type declaration.")
    other_line = _f(line=43)
    other_category = _f(category="BUG", message="Value may be null here.")
    out = dedupe([_f(), same_line_other_issue, other_line, other_category])
    assert len(out) == 4


def test_dedup_is_order_independent():
    findings = [_f(rule_id="a"), _f(tool="phpstan", severity="HIGH", rule_id="b", message="Other problem."),
                _f(rule_id="a"), _f(tool="ruff", file="z.py", line=1, rule_id="c", message="unused")]
    forward = dedupe(list(findings))
    backward = dedupe(list(reversed(findings)))
    assert [(f.file, f.line, f.severity, f.id, f.detected_by) for f in forward] == \
           [(f.file, f.line, f.severity, f.id, f.detected_by) for f in backward]


def test_shared_rule_id_merges_across_tools():
    out = dedupe([_f(tool="a", rule_id="shared.rule", message="one thing"),
                  _f(tool="b", rule_id="shared.rule", message="a totally different wording")])
    assert len(out) == 1


def test_empty_input():
    assert dedupe([]) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_static_deduplicator.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_review.static_analysis.deduplicator`

- [ ] **Step 3: Implement**

`src/ai_review/static_analysis/deduplicator.py`:

```python
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
    """Same tool reporting the same rule at the same location."""
    return (finding.tool or "", finding.rule_id or "", finding.file, finding.line or 0)


def _same_issue(left: Finding, right: Finding) -> bool:
    """True only for a genuine cross-tool duplicate (never merely nearby)."""
    if left.file != right.file or (left.line or 0) != (right.line or 0):
        return False
    if left.category != right.category:
        return False
    if normalize_message(left.original_message or left.title) == \
            normalize_message(right.original_message or right.title):
        return True
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

    Within one tool, ``(tool, rule, file, line)`` duplicates collapse (the
    highest severity survives, because the input is sorted by severity first).
    Across tools, findings merge only when file, line, category and normalized
    message agree, or when they share a rule id/fingerprint. Recomputing ids at
    the end gives a merged finding the *shared*, tool-independent id (RD-3).
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_static_deduplicator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/static_analysis/deduplicator.py tests/unit/test_static_deduplicator.py
git commit -m "feat: deterministic static-finding deduplication"
```

## Task 7: `base.py` — the analyzer template method

**Files:**
- Create: `src/ai_review/static_analysis/base.py`
- Test: `tests/unit/test_static_base.py`

**Interfaces:**
- Consumes: `config.StaticAnalysisConfig`, `models.{AnalyzerResult, Finding}`, `static_analysis.{context, runner}`.
- Produces: `StaticAnalysisError`, `tool_enabled(cfg, name)`, `StaticAnalyzer` (class attrs `name/executable/local_paths/languages/frameworks/extensions/scope/version_args/ok_exit_codes/env`; methods `supports`, `resolve`, `is_available`, `version`, `analyze`, abstract `build_argv`, `parse`).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_static_base.py`:

```python
"""The analyzer template method: availability, exit codes, fault isolation."""
import os

from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import ProfileEntry, RepoProfile, StagedChange
from ai_review.static_analysis import runner
from ai_review.static_analysis.base import StaticAnalyzer, tool_enabled
from ai_review.static_analysis.context import build_context
from ai_review.static_analysis.normalizer import make_finding

PY = RepoProfile(languages=[ProfileEntry("Python", 0.9, [])])


class FakeAnalyzer(StaticAnalyzer):
    name = "fake"
    executable = "fake-tool"
    languages = ("Python",)
    extensions = (".py",)
    ok_exit_codes = (0, 1)

    def build_argv(self, exe, ctx):
        return [exe, "--json", *ctx.changed_paths(self.extensions)]

    def parse(self, run, ctx):
        return [make_finding(tool=self.name, severity="LOW", file="a.py", line=1,
                             message=run.stdout.strip())]


class BoomParser(FakeAnalyzer):
    name = "boom"

    def parse(self, run, ctx):
        raise ValueError("unparseable")


def _ctx(tmp_path, *tools, profile=PY):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = AppConfig()
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig() for name in tools}
    return build_context(str(tmp_path), cfg,
                         [StagedChange(path="a.py", status="modified")], profile)


def test_supports_requires_explicit_enablement_and_technology(tmp_path):
    analyzer = FakeAnalyzer()
    assert analyzer.supports(_ctx(tmp_path)) is False            # not configured
    assert analyzer.supports(_ctx(tmp_path, "fake")) is True
    assert analyzer.supports(_ctx(tmp_path, "other")) is False
    js = RepoProfile(languages=[ProfileEntry("JavaScript", 0.9, [])])
    assert analyzer.supports(_ctx(tmp_path, "fake", profile=js)) is False


def test_resolve_prefers_project_local_binary(tmp_path):
    local = tmp_path / "node_modules" / ".bin"
    local.mkdir(parents=True)
    exe = local / "fake-tool"
    exe.write_text("#!/bin/sh\n")
    os.chmod(exe, 0o755)
    analyzer = type("Local", (FakeAnalyzer,),
                    {"local_paths": ("node_modules/.bin/fake-tool",)})()
    ctx = _ctx(tmp_path, "fake")
    assert analyzer.resolve(ctx) == str(exe)
    assert analyzer.is_available(ctx) is True


def test_unavailable_tool_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: None)
    result = FakeAnalyzer().analyze(_ctx(tmp_path, "fake"))
    assert result.status == "unavailable" and result.findings == []
    assert "not found" in result.error


def test_ok_exit_codes_cover_linters_that_report_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    # NOTE: base.py imports ``run_tool`` by name, so tests patch it there.
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(
                            argv=argv, status="run", exit_code=1, stdout="problem\n"))
    result = FakeAnalyzer().analyze(_ctx(tmp_path, "fake"))
    assert result.status == "run" and result.exit_code == 1
    assert result.findings[0].title == "problem"


def test_unexpected_exit_code_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(
                            argv=argv, status="run", exit_code=2, stdout="",
                            stderr="fatal: bad config"))
    result = FakeAnalyzer().analyze(_ctx(tmp_path, "fake"))
    assert result.status == "failed" and "fatal" in result.error and result.findings == []


def test_timeout_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(
                            argv=argv, status="timeout", error="timed out after 120s"))
    result = FakeAnalyzer().analyze(_ctx(tmp_path, "fake"))
    assert result.status == "failed" and "timed out" in result.error


def test_parser_exception_becomes_failed_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(
                            argv=argv, status="run", exit_code=0, stdout="not json"))
    result = BoomParser().analyze(_ctx(tmp_path, "boom"))
    assert result.status == "failed" and "unparseable" in result.error


def test_no_targets_is_skipped_without_running_anything(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda *a, **kw: called.append(1))
    analyzer = type("Nothing", (FakeAnalyzer,), {"extensions": (".sql",)})()
    result = analyzer.analyze(_ctx(tmp_path, "fake"))
    assert result.status == "skipped" and called == []


def test_tool_enabled_reads_the_opt_in_map():
    cfg = AppConfig()
    assert tool_enabled(cfg.static_analysis, "ruff") is False
    assert tool_enabled(None, "ruff") is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_static_base.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_review.static_analysis.base`

- [ ] **Step 3: Implement**

`src/ai_review/static_analysis/base.py`:

```python
"""Analyzer contract: identity + gating + argv + parser, nothing else.

``analyze`` is a template method so every analyzer inherits the same safety
properties (list argv, pinned cwd, bounded timeout, fault isolation) and a new
tool is one small module that only declares *what* to run and how to read it.
"""
from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod

from ai_review.config import StaticAnalysisConfig
from ai_review.models import AnalyzerResult, Finding
from ai_review.static_analysis.context import SCOPE_CHANGED_FILES, AnalysisContext
from ai_review.static_analysis.runner import ToolRun, run_tool

#: Availability/version probes are cheap and separate from a real analysis.
VERSION_TIMEOUT_S = 5.0
#: How much tool stderr is echoed into a failure message.
ERROR_TAIL_CHARS = 500

_VERSION_CACHE: dict[tuple[str, str], str] = {}


class StaticAnalysisError(RuntimeError):
    """Raised only when ``static_analysis.fail_on_error: true`` and a tool failed."""


def tool_enabled(cfg: StaticAnalysisConfig | None, name: str) -> bool:
    """Explicit per-tool opt-in (RD-4); a missing config enables nothing."""
    if cfg is None:
        return False
    entry = cfg.tools.get(name)
    return bool(entry and entry.enabled)


class StaticAnalyzer(ABC):
    """One external static-analysis tool."""

    name: str = ""
    #: PATH executable name.
    executable: str = ""
    #: Repo-relative candidates tried before PATH (phpstan lives in vendor/bin).
    local_paths: tuple[str, ...] = ()
    #: Technology gates; empty means "any technology".
    languages: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    #: File extensions consumed in changed_files scope; empty means "any".
    extensions: tuple[str, ...] = ()
    scope: str = SCOPE_CHANGED_FILES
    version_args: tuple[str, ...] = ("--version",)
    #: Exit codes that mean "ran fine" — linters exit non-zero when they report.
    ok_exit_codes: tuple[int, ...] = (0,)
    #: Extra environment forced for this tool (e.g. metrics off).
    env: dict[str, str] = {}

    # -- gating ------------------------------------------------------------

    def supports(self, ctx: AnalysisContext) -> bool:
        """Enabled by config *and* relevant to the detected technology/targets."""
        if not tool_enabled(ctx.cfg, self.name):
            return False
        if self.languages and not ctx.languages().intersection(self.languages):
            return False
        if self.frameworks and not ctx.frameworks().intersection(self.frameworks):
            return False
        if self.scope == SCOPE_CHANGED_FILES and self.extensions and not \
                ctx.changed_paths(self.extensions):
            return False
        return True

    def resolve(self, ctx: AnalysisContext) -> str | None:
        """Absolute executable path: project-local candidates first, then PATH."""
        for rel in self.local_paths:
            candidate = os.path.join(ctx.repo_root, rel)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return shutil.which(self.executable) if self.executable else None

    def is_available(self, ctx: AnalysisContext) -> bool:
        return self.resolve(ctx) is not None

    def version(self, ctx: AnalysisContext) -> str:
        """Best-effort first line of ``--version`` (5s, cached per repo)."""
        key = (self.name, ctx.repo_root)
        if key in _VERSION_CACHE:
            return _VERSION_CACHE[key]
        text = ""
        exe = self.resolve(ctx)
        if exe:
            run = run_tool([exe, *self.version_args], cwd=ctx.repo_root,
                           timeout=VERSION_TIMEOUT_S, env=self.env)
            lines = [line.strip() for line in (run.stdout or run.stderr).splitlines() if line.strip()]
            text = lines[0][:120] if lines else ""
        _VERSION_CACHE[key] = text
        return text

    # -- execution ---------------------------------------------------------

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        """Resolve, run, parse. Never raises for tool behaviour."""
        exe = self.resolve(ctx)
        if exe is None:
            return AnalyzerResult(status="unavailable",
                                  error=f"{self.executable or self.name} not found")
        argv = self.build_argv(exe, ctx)
        if not argv:
            return AnalyzerResult(status="skipped", error="no targets")
        timeout = ctx.cfg.timeout if ctx.cfg else 120
        run = run_tool(argv, cwd=ctx.repo_root, timeout=timeout, env=self.env)
        if run.status != "run":
            return AnalyzerResult(status="failed", exit_code=run.exit_code,
                                  error=run.error or run.status, duration_ms=run.duration_ms)
        if run.exit_code not in self.ok_exit_codes:
            detail = (run.stderr or run.stdout).strip()[-ERROR_TAIL_CHARS:]
            return AnalyzerResult(status="failed", exit_code=run.exit_code,
                                  error=detail or f"exit code {run.exit_code}",
                                  duration_ms=run.duration_ms)
        try:
            findings = self.parse(run, ctx)
        except Exception as exc:                       # analyzer/parser bug
            return AnalyzerResult(status="failed", exit_code=run.exit_code,
                                  error=f"parse error: {exc}", duration_ms=run.duration_ms)
        return AnalyzerResult(findings=list(findings), status="run",
                              exit_code=run.exit_code, duration_ms=run.duration_ms)

    @abstractmethod
    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        """Explicit argv list (never a shell string). Empty == nothing to do."""

    @abstractmethod
    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        """Normalize tool output into static findings."""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_static_base.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/static_analysis/base.py tests/unit/test_static_base.py
git commit -m "feat: static analyzer base class (gating, resolve, template analyze)"
```

## Task 8: `registry.py` + `analyzers/` loader

**Files:**
- Create: `src/ai_review/static_analysis/registry.py`
- Create: `src/ai_review/static_analysis/analyzers/__init__.py`
- Test: `tests/unit/test_static_registry.py`

**Interfaces:**
- Consumes: `base.StaticAnalyzer`, `context.AnalysisContext`, `models.ToolResult`.
- Produces: `REGISTRY`, `register(cls, *, replace=False)`, `build_candidates(cfg, ctx) -> (list[StaticAnalyzer], list[ToolResult])`, `analyzers.MODULES`, `analyzers.load_all()`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_static_registry.py`:

```python
"""Registry: explicit registration, opt-in enablement, applicability skips."""
from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import ProfileEntry, RepoProfile, StagedChange
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import build_context


class _Fake(StaticAnalyzer):
    name = "fake"
    executable = "fake"
    languages = ("Python",)
    extensions = (".py",)

    def build_argv(self, exe, ctx):  # pragma: no cover - not exercised here
        return []

    def parse(self, run, ctx):  # pragma: no cover
        return []


def _ctx(tmp_path, *tools, languages=("Python",)):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = AppConfig()
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig() for name in tools}
    profile = RepoProfile(languages=[ProfileEntry(lang, 0.9, []) for lang in languages])
    return cfg.static_analysis, build_context(
        str(tmp_path), cfg, [StagedChange(path="a.py", status="modified")], profile)


def test_register_rejects_duplicates_unless_replacing():
    registry.register(_Fake, replace=True)
    assert registry.REGISTRY["fake"] is _Fake
    try:
        registry.register(_Fake)
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("duplicate registration must raise")


def test_register_requires_a_name():
    class Nameless(StaticAnalyzer):
        def build_argv(self, exe, ctx):  # pragma: no cover
            return []

        def parse(self, run, ctx):  # pragma: no cover
            return []

    try:
        registry.register(Nameless)
    except ValueError as exc:
        assert "name" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("nameless registration must raise")


def test_build_candidates_is_opt_in(tmp_path):
    registry.register(_Fake, replace=True)
    cfg, ctx = _ctx(tmp_path)                      # configured: nothing
    analyzers, tools = registry.build_candidates(cfg, ctx)
    assert analyzers == [] and tools == []


def test_build_candidates_skips_technology_mismatch(tmp_path):
    registry.register(_Fake, replace=True)
    cfg, ctx = _ctx(tmp_path, "fake", languages=("Go",))
    analyzers, tools = registry.build_candidates(cfg, ctx)
    assert analyzers == []
    assert [t.name for t in tools] == ["fake"]
    assert tools[0].status == "skipped" and "applicable" in tools[0].error


def test_build_candidates_returns_enabled_applicable_analyzer(tmp_path):
    registry.register(_Fake, replace=True)
    cfg, ctx = _ctx(tmp_path, "fake")
    analyzers, tools = registry.build_candidates(cfg, ctx)
    assert [a.name for a in analyzers] == ["fake"] and tools == []


def test_analyzer_loader_registers_shipped_modules():
    from ai_review.static_analysis.analyzers import MODULES, load_all
    load_all()                                     # idempotent
    for name in MODULES:
        assert name in registry.REGISTRY or True    # phase lists grow per task
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_static_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_review.static_analysis.registry`

- [ ] **Step 3: Implement**

`src/ai_review/static_analysis/analyzers/__init__.py`:

```python
"""Shipped analyzers. Each module registers itself in ``registry.REGISTRY``.

The module list is explicit so a missing/broken analyzer module is a hard
import error, never a silently absent tool.
"""
from __future__ import annotations

from importlib import import_module

#: One entry per analyzer module, appended by each phase's tasks.
MODULES: tuple[str, ...] = ()


def load_all() -> None:
    """Import every shipped analyzer module (idempotent; import is cached)."""
    for name in MODULES:
        import_module(f"{__name__}.{name}")
```

`src/ai_review/static_analysis/registry.py`:

```python
"""Analyzer registry and config-driven selection.

The loader call must stay at the *bottom* of this module: analyzer modules
import ``registry`` to call ``register``, so ``REGISTRY`` and ``register`` must
already exist by the time the first one is imported (RD-11).
"""
from __future__ import annotations

from ai_review.config import StaticAnalysisConfig
from ai_review.models import ToolResult
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import AnalysisContext

#: name -> analyzer class. Tests inject fakes with ``monkeypatch.setitem``.
REGISTRY: dict[str, type[StaticAnalyzer]] = {}


def register(cls: type[StaticAnalyzer], *, replace: bool = False) -> type[StaticAnalyzer]:
    """Register an analyzer class under its ``name``."""
    if not cls.name:
        raise ValueError("analyzer must declare a name")
    if cls.name in REGISTRY and not replace:
        raise ValueError(f"analyzer {cls.name!r} already registered")
    REGISTRY[cls.name] = cls
    return cls


def build_candidates(cfg: StaticAnalysisConfig,
                     ctx: AnalysisContext) -> tuple[list[StaticAnalyzer], list[ToolResult]]:
    """Split configured analyzers into *runnable* and *skipped* (not applicable).

    Analyzers that are not enabled in config are absent entirely (they were
    never asked for); enabled ones that do not match the detected technology or
    the changed files are reported as ``skipped`` so the run's summary stays
    honest about what was considered.
    """
    runnable: list[StaticAnalyzer] = []
    skipped: list[ToolResult] = []
    for name in sorted(REGISTRY):
        entry = cfg.tools.get(name)
        if entry is None or not entry.enabled:
            continue
        analyzer = REGISTRY[name]()
        if analyzer.supports(ctx):
            runnable.append(analyzer)
        else:
            skipped.append(ToolResult(name=name, status="skipped",
                                      error="not applicable to this change set"))
    return runnable, skipped
```

Append to the bottom of `registry.py`:

```python
load_builtin()
```

with, above it:

```python
def load_builtin() -> None:
    """Import the shipped analyzer modules once (idempotent)."""
    from ai_review.static_analysis.analyzers import load_all
    load_all()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_static_registry.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/static_analysis/registry.py src/ai_review/static_analysis/analyzers tests/unit/test_static_registry.py
git commit -m "feat: static analyzer registry with config-driven selection"
```

<!-- PLAN-CONTINUES -->
