# Static Analysis Layer — Design (Adaptation for `ai-review`)

Date: 2026-09-17
Status: Draft for review
Upstream spec: [`static-analysis.md`](../../../static-analysis.md)

## 1. Purpose

This document adapts the upstream static-analysis spec to the existing
`ai-review` architecture. It locks down the decisions the spec leaves open
("adapt to the project's conventions") and records invariant-preserving
placements. The upstream spec remains the source of truth for goals,
requirements, and priorities; this document resolves *how* they fit this
codebase.

## 2. Verified baseline (from `AGENTS.md` + code)

- Pipeline composition root: `src/ai_review/pipeline.py`. Order today: collect
  staged → detect/profile → classify → diff → redact → `scan_staged` (security)
  → `scan_unresolved` (resolution) → prompt → `ReviewSession.run` → `validate_findings` → `PolicyEngine.decide`.
- `Finding` is a single dataclass (UPPER severity, category enum, file/line,
  title/description/evidence/recommendation, confidence float, is_pre_existing,
  hard_block). Security and resolution findings are `Finding`s prepended to
  `result.issues`. The LLM schema (`OUTPUT_JSON_SCHEMA` in `prompts.py`) and
  `parse_llm_json` (`parser.py`) build them.
- `validate_findings` (validator.py) drops any non-`hard_block` finding not
  anchored to a staged changed new-line; BLOCK downgrades to WARN when nothing
  blocking survives. `PolicyEngine` (policy.py) decides BLOCK/WARN/PASS purely
  from `result.issues`; choices: OPTION `hard_block` / severity in `block_on` /
  confidence ≥ threshold, pre-existing never blocks.
- `Pipeline.run()` returns `ReviewResult | str` (dry-run plan text; provider never invoked).
- CLI contract: handled errors print `error: ...` to stderr, exit 2; 0 = pass/warn, 1 = blocked; `--doctor` reuses 1 for "not ready".
- Config layering: `config/default.yaml` < `AI_REVIEW_ORG_CONFIG` < `~/.config/ai-review/config.yaml` < repo `.ai-review.yaml` < CLI `--config key=value`; org `enforce` cannot be weakened.
- Package data: assets under `src/ai_review/{prompts,config}` ship via hatch,
  read relative to `__file__`, guarded by `tests/unit/test_package_data.py`.
- Tests: `tests/unit/` hermetic, `tests/integration/test_pipeline.py` builds real
  git repos under `tmp_path`, providers faked (`StaticProvider`, `RecordingProvider`,
  `DeadProvider`). Fully offline.

## 3. Key decisions (resolved here)

### D1. Static findings are evidence, never a gate

Static-analysis findings exist so the AI has deterministic evidence. They do
**not** enter `result.issues`, so `validate_findings` and `PolicyEngine` cannot
turn a tool report into a block. Only an AI finding that survives the existing
`validate_findings` line-anchor + policy gate decides blocking (spec §25, §35).

Implementation: review outcome carries a new `ReviewResult.static` summary;
static findings live there, separate from `issues`. This preserves every
AGENTS.md invariant: security (`scan_staged`) and resolution (`scan_unresolved`)
remain the only offline hard-blocking gates, unchanged, running before the LLM.

### D2. Single finding model — extend, don't duplicate

Per spec §18/§19/§26: no competing model. Extend `Finding` (models.py) with
optional, defaulted provenance fields:

- `source: str = "ai"` (`"ai" | "static_analysis"`)
- `id: str | None` — stable static-finding id (assigned by the deduplicator, short deterministic hash)
- `tool: str | None`, `rule_id: str | None`, `fingerprint: str | None`
- `original_severity: str | None`, `original_message: str | None`
- `detected_by: list[str]` — merged-tool provenance after dedup (spec §22)
- `related_static_finding_id: str | None` — an AI issue that confirms/references a static finding
- `confidence`, `category`, `line`, etc. already exist and are reused.

Existing call sites that construct `Finding(...)` keep compiling unchanged
(defaults); `render_json`/`render_terminal`/`render_markdown` only gain the new
fields in the new `static` block, never in the existing per-issue rendering.

### D3. Static finding values map onto existing enums

Static findings reuse `Severity` (CRITICAL/HIGH/MEDIUM/LOW/INFO) and `Category`.
Normalizer maps each tool's native severities (e.g. ESLint `2`/`1`/`0`,
Semgrep `ERROR`/`WARNING`/`INFO`, PHPStan `error`/`warn`) through a per-tool
explicit table (`normalizer.py`). The original severity string is retained in
`original_severity`; confidence uses the existing `Finding.confidence` float
(per-tool map high/medium/low → e.g. 0.9/0.6/0.4).

### D4. AI verdict protocol

The LLM JSON response gains an optional top-level array `static_assessments`
(entries `{finding_id, verdict, reason}`) and each issue may carry
`related_static_finding_id`. New `StaticAssessment` dataclass on `ReviewResult`.
Two validator additions (after the existing anchor logic):

1. `related_static_finding_id` on a kept AI issue must reference an existing
   static finding id in `result.static`; unknown ids drop the link (issue kept).
2. `static_assessments` entries whose `finding_id` is unknown are dropped;
   the rest are stored on `ReviewResult.static_assessments` so rendering can
   show "Detected by Semgrep · Confirmed by AI".

`StaticAssessment.verdict` uses the spec §27 vocabulary: `confirmed`,
`likely_true`, `uncertain`, `likely_false_positive`, `false_positive`.

### D5. Placement in the pipeline (invariant-preserving)

Single insertion point, after technology detection and before the hard gates
(matching the upstream diagram):

```
collect staged
  → detect / profile_from_names
  → classify
  → NEW static analysis: registry → runner(concurrent) → parser → normalize → dedup
  → security scan (unchanged, hard-block)
  → resolution gate (unchanged, hard-block)
  → prompt build (+ compact static findings section, + verdict schema)
  → ReviewSession.run (LLM)
  → validate_findings (+ static-id link checks)
  → PolicyEngine.decide (from result.issues only)
```

Static analysis runs inside `Pipeline._prepare()` — the offline stage that the
dry-run path also executes, so `--dry-run` reflects real steps and still never
calls the provider. Static findings never block, so this placement cannot
weaken the security/resolution gates (they still run, still hard-block).

### D6. Scope: changed-files vs project

Each analyzer declares `scope` per spec §17; the runner passes the working-tree
contents of staged files (`changed_files`) or the whole repo root (`project`).
Default = analyze only files relevant to the analyzer's detected languages.

### D7. Flags-only CLI (spec §29)

No subcommand added. New flags:
- `--no-static-analysis` — equivalent to `--static-only=false`; disables static analysis for this run.
- `--static-only` — run offline stages, report (static + security + resolution findings), skip the LLM. Exit semantics preserved: security/resolution hard-blocks still exit 1; tool findings never block.
- `--doctor` (existing) gains a "Static Analysis" section: each configured tool → `✓` available / `⚠ not installed` / `⚠ not configured` / `✗ error`. Availability of optional tools is a warning, never an error exit (spec §15, current doctor precedent with the LLM probe).

## 4. New package: `src/ai_review/static_analysis/`

```
static_analysis/
  __init__.py        # public API: run_static_analysis(cfg, ctx), build_summary
  base.py            # StaticAnalyzer base class + ToolResult + scope constants
  context.py         # AnalysisContext: repo_root, changes, profile, lang/fw names, diff_text, cfg
  registry.py        # analyzer registry: register()/registered()/build_enabled(cfg, profile)
  runner.py          # safe subprocess runner (argv list, timeout, capture, env, exit code)
  normalizer.py      # severity/confidence/rule maps, stable finding id
deduplicator.py    # deterministic dedup (tool-rule-file-line key; cross-tool merge by file+line+category)
    doctor.py          # per-tool availability/version table for --doctor
  analyzers/
    semgrep.py phpstan.py eslint.py ruff.py staticcheck.py          # Phase 1
    larastan.py typescript.py govet.py sqlfluff.py hadolint.py trivy.py  # Phase 2
    codeql.py sonarqube.py checkov.py tflint.py kubeconform.py kube_linter.py  # Phase 3
```

No analyzer-specific logic enters `pipeline.py`; the pipeline calls
`run_static_analysis(cfg, ctx)` and renders the compact prompt section.
Adding a tool = one `analyzers/<name>.py` module registered into `registry.py`.

### Analyzer base contract

```python
class StaticAnalyzer:
    name: str
    languages: tuple[str, ...] = ()          # "PHP", "JavaScript", ..., () = universal
    frameworks: tuple[str, ...] = ()          # "Laravel", ... () = any
    scope: str = "changed_files"              # "changed_files" | "project"
    output_format: str = "json"               # "json" | "text"

    def is_available(self) -> bool            # shutil.which / version probe, cached
    def supports(self, ctx: AnalysisContext) -> bool   # enabled + technology match
    def run(self, ctx: AnalysisContext) -> list[Finding]   # build argv, call runner, parse, normalize
```

### Runner contract

`run_static_analysis` executes enabled analyzers whose `supports(ctx)` is true,
concurrently (`ThreadPoolExecutor`, `cfg.concurrency`, default 4). Each runs via
`runner.py`:

- `argv` lists only (no `shell=True`, no interpolation)
- per-tool `timeout` (default `cfg.timeout`, 120s) — timeout → `failed` status, finding-free
- capture stdout/stderr/exit code; controlled env (inherited env, `cwd=repo_root`)
- `is_available()` gates execution; unavailable → `ToolResult(status="unavailable")`
- analyzers never execute project scripts (`npm run`, `composer run`, Makefiles,
  application startup) — only the tool binary itself with explicit args (spec §16)

`ToolResult` per tool: name, status (`run`/`unavailable`/`skipped`/`failed`), exit_code, stderr tail, duration_ms. `StaticAnalysisSummary` aggregates: findings, tools, files_analyzed (distinct files passed to at least one executed analyzer), duration_ms, derived counts (critical/high/medium/low/info, per-tool run/available/unavailable).

## 5. Analyzer matrix (17, per spec §37 phases)

| Tool | Phase | Lang/FW | Scope | Invocation (JSON-preferred) | Notes |
|---|---|---|---|---|---|
| semgrep | 1 | universal | changed_files | `semgrep --json --quiet` (config: default rules if present; `--config auto` only when configured) | never downloads DB; JSON output |
| phpstan | 1 | PHP | project | `vendor/bin/phpstan analyse --error-format=json <files-or-path>` | project-aware; absent vendor → unavailable |
| eslint | 1 | JS/TS | changed_files | `eslint --format json <files>` | respects existing config |
| ruff | 1 | Python | changed_files | `ruff check --output-format json <files>` | lightweight primary |
| staticcheck | 1 | Go | project | `staticcheck -f json ./...` | |
| larastan | 2 | PHP+Laravel | project | `vendor/bin/larastan analyse --error-format=json` | only when Laravel detected |
| typescript | 2 | TypeScript | project | `tsc --noEmit` (respects `tsconfig.json`) | text diagnostics; no application execution |
| govet | 2 | Go | project | `go vet ./...` | |
| sqlfluff | 2 | SQL | changed_files | `sqlfluff lint --format json <sql files>` | only .sql files |
| hadolint | 2 | Docker | changed_files | `hadolint --format json <Dockerfiles>` | Dockerfile paths only |
| trivy | 2 | infra | project | `trivy config --format json .` (IaC) | default disabled; no DB download in pre-commit |
| codeql | 3 | universal | project | configured (databases/`--threads`); | default disabled, flagged CI-only |
| sonarqube | 3 | universal | project | via configured server/CLI | requires explicit config+server, then optional |
| checkov | 3 | infra | project | `checkov -d . --compact -o json` | TF/k8s/cloud |
| tflint | 3 | Terraform | project | `tflint --format json` | |
| kubeconform | 3 | Kubernetes | changed_files | `kubeconform <manifests>` | |
| kube-linter | 3 | Kubernetes | project | `kube-linter lint <manifests>` | |

Severity normalization lives in each analyzer's `SEVERITY_MAP` (explicit per
tool; never an assumed `ERROR→CRITICAL` universal rule). CodeQL/SonarQube are
never required and never run without explicit config.

## 6. Findings model (extended `Finding`, lives in `models.py`)

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
    # NEW (all defaulted — backward compatible):
    source: str = "ai"                     # "ai" | "static_analysis"
    id: str | None = None                  # static finding stable id
    tool: str | None = None
    rule_id: str | None = None
    fingerprint: str | None = None
    original_severity: str | None = None
    original_message: str | None = None
    detected_by: list[str] = field(default_factory=list)
    related_static_finding_id: str | None = None
```

New top-level models:

```python
@dataclass
class ToolResult:
    name: str
    status: str              # "run" | "unavailable" | "skipped" | "failed"
    exit_code: int | None = None
    error: str = ""
    duration_ms: int = 0

@dataclass
class StaticAnalysisSummary:
    findings: list[Finding]
    tools: list[ToolResult]
    files_analyzed: int = 0
    duration_ms: int = 0

@dataclass
class StaticAssessment:
    finding_id: str
    verdict: str             # confirmed | likely_true | uncertain | likely_false_positive | false_positive
    reason: str = ""
```

`ReviewResult` gains:
- `static: StaticAnalysisSummary | None = None`
- `static_assessments: list[StaticAssessment] = field(default_factory=list)`

## 7. Prompt integration (`prompts.py`)

- `PromptBuilder.build(...)` gains a `static_text: str = ""` parameter; when
  non-empty it renders into the user prompt as:

  ```
  ## Static Analysis Findings
  [HIGH] PHPStan
  File: app/Services/OrderService.php:142
  Rule: argument.type
  Message: ...

  [MEDIUM] Semgrep
  File: app/Http/Controllers/UserController.php:57
  ...
  ```

  produced by a compact formatter (`format_static_findings(summary)`) that lists
  tool, severity, file:line, rule_id, message — truncated to a budget (per
  finding and total), never raw analyzer dumps (spec §24/§38).
- `system.md` gains a line: "Static-analysis findings are evidence, not truth.
  Verify each against the source; classify false positives; suggest fixes."
  (Also covers spec §25/§27.) Asset edits must keep `test_package_data.py`
  guards green.
- `OUTPUT_JSON_SCHEMA` (prompts.py) gains optional `static_assessments` array
  and `related_static_finding_id` on issues; `parser.py` parses both, validates
  verdict enum, populates `ReviewResult.static_assessments` and the issue link.
- Corrective-retry path (ReviewSession) needs no change (schema travels with payload).

## 8. Config (`config.py` + `config/default.yaml`)

```yaml
static_analysis:
  enabled: true
  fail_on_error: false        # true: analyzer run/parse errors become CLI exit 2 (default: warn)
  timeout: 120                # per-tool seconds
  concurrency: 4
  tools:
    semgrep: { enabled: true }
    phpstan: { enabled: true }
    eslint:  { enabled: true }
    ruff:    { enabled: true }
    staticcheck: { enabled: true }
    larastan:   { enabled: true }
    typescript: { enabled: true }
    govet:      { enabled: true }
    sqlfluff:   { enabled: true }
    hadolint:   { enabled: true }
    trivy:      { enabled: false }   # IaC config scan; no DB download during pre-commit
    codeql:     { enabled: false }   # CI-only
    sonarqube:  { enabled: false }   # CI-only, requires server
    checkov:    { enabled: true }
    tflint:     { enabled: true }
    kubeconform:{ enabled: true }
    kube_linter:{ enabled: true }
```

`StaticAnalysisConfig` pydantic model wired into `AppConfig`. Per-tool
`enabled` is a boolean (future: args/options). Follows existing layering;
organization `enforce` unaffected (no new org directives in this iteration).

## 9. CLI (`cli.py`)

- `--no-static-analysis`: pushes `static_analysis.enabled=False` override into config.
- `--static-only`: builds pipeline with a `static_only` option; `run()` stops
  after `_prepare()` + security/resolution and renders a report (static +
  security + resolution findings). Never calls the provider. Exit 0/1/2 semantics
  preserved: only real hard-block findings (security/resolution) can yield 1.
- `--doctor`: appends the Static Analysis section via `static_analysis.doctor.py`.
- Exit contract unchanged (0/1/2; `--doctor` 1 = not ready). Analyzer failures
  never surface as CLI errors unless `fail_on_error` is set (default behavior: warn).

## 10. Output (`output.py`)

- `render_terminal`: adds a `Static Analysis` block (per-tool ✓/✗ + per-finding
  severity lines) between summary and AI issues; unchanged when `result.static` is None.
- `render_json`: adds `static` object (summary + findings incl. provenance fields)
  and `static_assessments` when present; otherwise output shape is unchanged.
- `render_markdown`: adds a Static Analysis Findings table.
- Existing per-issue rendering unchanged, so CI consumers of the old JSON shape
  are unaffected when static analysis is disabled.

## 11. Deduplication (`deduplicator.py`)

Deterministic, order-independent:
1. Within a tool: key = `(tool, rule_id, file, line)`; later duplicates dropped.
2. Across tools: merge when `file == file` and `line == line` and
   `category == category` and normalized messages norm-equal (lower, strip,
   collapse whitespace; fallback: shared rule_id or shared cwe). Merged finding
   keeps the highest severity, union of `detected_by`, one stable `id`.
3. Restful independent findings (different file/line/category) never merge
   (spec §22 "do not blindly merge findings that are merely near each other").

## 12. Testing strategy (fully offline)

- New `tests/unit/test_static_analysis/` mirroring the package:
  - `test_registry.py` — registration, lookup, disabled analyzer, supports() gating
  - `test_runner.py` — success, non-zero exit, timeout (monkeypatched `subprocess.run`), malformed/empty output
  - `test_normalizer.py` — severity/confidence maps, original preserved, id stability
  - `test_deduplicator.py` — exact / near / independent
  - `test_parsers.py` — per-tool fixture outputs (JSON strings checked in as fixtures under `tests/fixtures/static_analysis/`)
  - `test_context.py` / `test_analyzers*.py` — per-analyzer parser + argv build, `is_available()` patched
  - `test_config.py` (extended) — default tool flags wiring
- `tests/integration/test_pipeline.py` (extended):
  - static findings appear in the LLM payload (assert `## Static Analysis Findings` + a semgrep fixture line in `RecordingProvider.payloads[0].user`)
  - `static_only` path never calls provider (DeadProvider must not raise
    `LlamaServerNotFound`; exit reflects only security/resolution)
  - `--no-static-analysis` restores the exact old prompt (assert absence)
  - verdict/`related_static_finding_id` validation drops unknown ids
  - full suite green: `.venv/bin/python -m pytest` (currently 333)
- Manual verification: `.venv/bin/ai-review --doctor`, `.venv/bin/ai-review --dry-run`.

## 13. Backward compatibility & invariants checklist

- [x] `Pipeline.run()` returns `ReviewResult | str`; dry-run never touches provider
- [x] Security scan + resolution gate remain pre-LLM and hard-blocking (untouched logic)
- [x] Static findings never enter `result.issues`; only AI issues gate via policy
- [x] Exit codes 0/1/2 and error contract unchanged
- [x] Config layering + org `enforce` unchanged
- [x] `Finding`/`ReviewResult` changes are additive (defaulted fields); existing
      tests keep passing without edits
- [x] Optional analyzer absence never fails a commit (default `fail_on_error: false`)
- [x] New prompt/schema text stays covered by `test_package_data.py` guards

## 14. Out of scope (this iteration)

- Analyzers not in spec §37 phases: phpcs, mypy, pylint, checkstyle, pmd,
  spotbugs, clang-tidy, cppcheck, rubocop, brakeman. The registry/supports pattern
  makes adding any of these a one-module change.
- Per-tool `args`/custom-rule configuration beyond on/off.
- SARIF output (spec §30): deferred; normalized model is format-independent so
  `analysis.json` + future SARIF follow the same `parser → normalize` path.
- AI fixing code; ONLY evidence + verdicts.
- Caching of analyzer results (out of the existing cache key design).

## 15. Implementation order (single mega-plan, 3 phases; each phase ends green)

1. Framework: models, `static_analysis/` package (base, context, registry,
   runner, normalizer, deduplicator, doctor), config, CLI flags, output,
   prompt/schema extension, validator links, pipeline wiring, integration tests.
2. Phase 1 analyzers (semgrep, phpstan, eslint, ruff, staticcheck) + parser tests.
3. Phase 2 analyzers (larastan, typescript, govet, sqlfluff, hadolint, trivy).
4. Phase 3 analyzers (codeql, sonarqube, checkov, tflint, kubeconform, kube-linter).
5. Final: `.venv/bin/python -m pytest`, `--doctor`, `--dry-run`, report.