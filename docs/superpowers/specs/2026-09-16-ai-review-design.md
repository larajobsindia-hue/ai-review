# AI Review Agent — Design Specification

Date: 2026-09-16
Status: Approved
Source requirement: `prompt.md` (AI Git Pre-Commit Review Agent, 34 sections)

## 1. Overview

Build **`ai-review`**: a production-quality, technology-agnostic AI-powered Git
pre-commit code review agent designed for organization-wide developer use. It
inspects staged changes before a commit and uses a configurable LLM to surface
actionable issues, returning PASS / BLOCK / WARN. The normal developer workflow
is unchanged: `git add . && git commit`. A pre-commit hook invokes the reviewer
automatically; `git commit --no-verify` is never interfered with.

The tool is technology-agnostic about the repositories it reviews (detection is
dynamic, never hardcoded to a stack) and provider-agnostic about the LLM
(common interface, first provider llama.cpp, local-first default).

## 2. Key Decisions (confirmed with stakeholder)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Implementation language | Python 3.10+ |
| D2 | Delivery scope | Full 34-section spec defined now; implemented incrementally |
| D3 | Distribution | PyPI / private index package; `pipx` / `uv tool install`; console entry point `ai-review`; hook wraps the executable |
| D4 | LLM output conformance | Constrained generation when available (llama.cpp JSON-schema grammar, `response_format: json_object`), plus parse-and-retry corrective pass as universal fallback |
| D5 | Architecture | Functional pipeline (Approach 1); `httpx` sync now / async later for parallel chunked review |

## 3. Architecture

Functional pipeline of stages, each a plain function or small single-`run()`
class with explicit data contracts. The composition root is `pipeline.py`;
`cli.py` never wires stages itself.

```text
git_collector → [StagedChange]
diff_collector → ReviewRequest.diff
detector → RepoProfile
classifier → per-file change type
context_engine → [ReviewContext]
check_runner → [CheckResult]
security_gate → sanitized payload (before ANY provider call)
prompt_builder → Prompt
reviewer (provider gate) → RawLLMResponse
parser → ReviewResult
validator → validated ReviewResult
policy_engine → final decision (PASS / BLOCK / WARN)
output → terminal | json | markdown
```

### 3.1 Package layout

```text
ai_review/
├── __init__.py (version)
├── cli.py            # argparse, command dispatch, exit codes
├── pipeline.py       # orchestration / composition root
├── models.py         # all data contracts
├── config.py         # layered config load + merge + validation
├── git.py            # safe subprocess git collection
├── diff.py           # diff parsing, stats, hunks, file classification
├── detector.py       # evidence-based technology detection
├── profile.py        # RepoProfile construction + deduction
├── classifier.py     # change-type / review-depth classification
├── context.py        # context engine (containing function/module/imports)
├── checker.py        # deterministic check runner
├── security.py       # secret redaction + pre-commit secret scan
├── prompts.py        # layered prompt assembly + cache key
├── reviewer.py       # provider selection + invoke + retry
├── parser.py         # JSON parse + schema validation + corrective retry
├── validator.py      # finding validation (file/line/severity/confidence)
├── policy.py         # policy engine → decision
├── output.py         # terminal / JSON / Markdown renderers
├── doctor.py         # --doctor probes
├── hooks.py          # pre-commit install / uninstall
├── cache.py          # local review cache (Phase 6)
└── providers/
    ├── base.py       # LLMProvider interface
    ├── llamacpp.py   # llama.cpp; optional JSON-schema grammar
    └── openai_compatible.py  # OpenAI-compatible / Azure / internal endpoints
```

Analyzers (technology prompt plugins) and checks live in separate modules as
they are added in later phases.

### 3.2 Data contracts (models.py)

- `StagedChange` — path, status (added/modified/deleted/renamed), is_binary,
  old_path/new_path, diff hunks, stat (+/-)
- `RepoProfile` — languages / frameworks / databases / infrastructure; each
  entry `name + confidence + evidence[]`
- `ReviewRequest` — files, profile, classified types, diff payload, context
  bundles, applicable prompt layers, config snapshot for cache key
- `Finding` — severity, category, file, line, title, description, evidence,
  recommendation, confidence, is_pre_existing
- `ReviewResult` — decision (PASS/BLOCK/WARN), summary, issues[]
- `ReviewContext` — per changed hunk: surrounding lines, containing
  function/module, relevant imports
- `CheckResult` — name, command, exit_code, output, traced
- `Prompt` — system + user text, expected JSON schema, provider hints

All contracts validated with pydantic where they cross trust boundaries
(config, LLM output); dataclasses elsewhere.

## 4. Data Flow

1. `git diff --cached` (staged only) via safe subprocess — never `shell=True`,
   no string-interpolated commands.
2. Detect technologies from evidence (extensions, manifests, lock files,
   config/build/CI/Docker files) with confidence; build `RepoProfile`.
3. Classify changed files → review depth (source / test / config / docs /
   migration / infra / dependency / generated / lock / binary / unknown).
4. Gather context around changed regions (Phase 2) with configured limits.
5. Run applicable deterministic checks via auto-detect (Phase 3).
6. Secret-scan + redact staged content (before any provider call).
7. Assemble layered prompt; invoke provider; parse JSON; corrective retry once
   on conformance failure; validate findings.
8. Policy engine returns final decision; render output; set exit code.

## 5. Providers

- Interface: `LLMProvider.review(request) -> RawLLMResponse` (text + metadata).
- Factory keyed on `llm.provider` config value (`llamacpp` default).
- `llamacpp.py` — `http://127.0.0.1:8080/v1/chat/completions` default endpoint
  (configurable), optional API key, optional llama.cpp **grammar** supplied as
  a JSON schema (constrained generation).
- `openai_compatible.py` — configurable base URL for internal company endpoints,
  hosted OpenAI-compatible providers, Azure-flavored endpoints; optional
  `Authorization` header; uses `response_format={"type":"json_object"}` when the
  endpoint advertises JSON mode support.
- Config: `llm.provider`, `llm.endpoint`, `llm.model` (default `auto`, never
  hardcoded), `llm.timeout_seconds` (default 120), `llm.max_tokens` (default
  4096), plus provider-specific auth/headers. Everything configurable per-layer.

## 6. Output Conformance (D4)

Chain: `parser` (schema-validated parse) → on failure, one corrective retry
("Your last output was not valid JSON matching the supplied schema. Return only
the JSON.") → on second failure, treat as **LLM failure** → failure policy, never
a forced PASS. JSON mode / grammar are capabilities, used when advertised, and
the parse-and-retry fallback is universal.

## 7. Prompt Architecture

Layered: `system.md` → `review.md` (generic rules) → `technology/<lang>.md`
(only detected languages, never irrelevant stacks) → repository rules blob
(from `.ai-review.yaml`) → staged diff → relevant context. Plain-file prompts
under `prompts/`; shipped defaults with the package, overridable in
`~/.config/ai-review/prompts/` and `.ai-review/prompts/`. Prompt hash + diff
hash + config hash compose the cache key (Phase 6).

The generic system prompt enforces: review only staged changes + supplied
context; report only real, actionable, evidence-backed findings; exclude style
preferences, trivial naming, speculative/hypothetical issues, and unrelated
pre-existing bugs; uncertain ⇨ lower confidence; return **only valid JSON**
matching the schema.

## 8. Deterministic Checks (Phases 1 & 3)

- Phase 1 ships the bundled **pre-commit secret scan** (works offline, catches
  hard secret commits even when the LLM is unreachable).
- Phase 3 adds the auto-detect runner: resolve tools via `which`/PATH
  (platform-independent), run only tools relevant to the `RepoProfile`, respect
  `checks.enabled` / `checks.auto_detect`, merge org-trusted commands with
  repo-untrusted commands.
- Security model: `checks.trusted_commands` (org) vs repository-provided
  `checks.commands` (untrusted). Untrusted commands never run implicitly above a
  configured allowlist; explicitly separated and clearly surfaced. No arbitrary
  repo command runs by default.

## 9. Security Model

- Redaction (deterministic, reversible-proof, never logs originals) for: env
  var values, passwords, API keys/Bearer tokens, AWS/GCP/Azure credentials,
  private keys (PEM/SSH), JWT/OAuth secrets, connection strings.
- `security.excluded_files`: `.env`, `*.pem`, `*.key`, etc. — content never sent.
- Redaction runs **before** any provider call and again on response output.
- Never log source code by default; verbose mode gated and explicit.
- `.env` and key-like files are never sent even if not staged-excluded.
- Org enforcement: when `organization.enforce` is true, repo config cannot
  silently weaken mandatory security rules.

## 10. Policy Engine

- Config: `policy.block_on` (default CRITICAL, HIGH), `policy.minimum_confidence_to_block`
  (default 0.80).
- Severities: CRITICAL / HIGH / MEDIUM / LOW / INFO.
- A finding must satisfy: evidence + relevance to staged change + sufficient
  confidence. "Could theoretically fail" never blocks.
- Pre-existing problems: forced to INFO unless the diff introduces/exposes/
  worsens/depends directly on them (LLM-declared `is_pre_existing`, validated).
- Findings validated before blocking: file exists, line in staged range, file in
  staged set, severity/confidence valid.
- Optional second-pass validation reviewer for HIGH/CRITICAL before blocking
  (config-gated `validation.validator: auto|always|off`, latency trade-off).
- **The policy engine, not the LLM, produces the Git decision.** Never report
  "passed" when the LLM never ran.

## 11. Failure Policy

`failure_policy.on_llm_unavailable` = `block` | `warn` (default) | `allow`.
Explicit, honest messaging (e.g. "AI review was skipped. Commit allowed because
failure_policy=warn"). Never claim an AI review passed when the call failed.

## 12. Git Integration & Hooks

- Collect: repo root, branch, staged files, status, diff stats, diff content.
- Handle: added / modified / deleted / renamed / binary / large files.
- Safe subprocess via `subprocess.run([...])`, cwd pinned, no shell.
- Large-file / binary handling: excluded from LLM payload, noted in output.
- Hook install (`--install-hook`): detect existing `.git/hooks/pre-commit`;
  never silently overwrite; back up; wrap; add identifiable marker
  (`# ai-review hook: v<version> :: do-not-edit`); `--uninstall-hook` removes
  only our integration; `git commit --no-verify` untouched.

## 13. Configuration & Precedence

YAML config layers with precedence (high → low):

```text
CLI arguments
  ↓
Repository config      .ai-review.yaml (+ .ai-review/prompts/)
  ↓
User config           ~/.config/ai-review/config.yaml
  ↓
Organization defaults <org config path>/config.yaml
```

Repository config may customize: technologies, review rules, ignored files,
severity policy, static checks, LLM provider, context limits. Organization
policy enforces minimum security requirements; with `organization.enforce`
enabled, lower layers cannot weaken mandatory rules. Merged config validated
with pydantic; unknown keys warn.

## 14. Large Changes / Chunking (Phase 5)

- `review.max_diff_kb` (default 200); `review.chunking.enabled` + `max_chunk_kb`
  (default 50).
- Logical grouping (backend / frontend / database / infrastructure) never splits
  inside a function or logical block; parallel chunk review via `httpx`
  async client; aggregated findings feed one policy decision.

## 15. Caching (Phase 6)

- Local cache keyed on `hash(staged diff + relevant context + configuration +
  prompt version)`. Cache metadata, not source content. Config `cache.enabled`.

## 16. Privacy

- Local mode (default): `repo → agent → local llama.cpp`; no source leaves the
  machine. Documented.
- Remote mode (configured remote provider): `repo → agent → secret redaction →
  remote LLM`; output clearly informs the developer that code is sent to the
  remote provider (startup banner + doctor + docs).

## 17. CLI

```text
ai-review                       # run staging review (assumes pre-commit context)
ai-review --staged              # explicit staged review
ai-review --help | --version
ai-review --doctor              # env/repo/config/detection/LLM/auth/model/prompt/hook probes
ai-review --install-hook | --uninstall-hook
ai-review --dry-run             # show would-be steps, no external calls
ai-review --verbose
ai-review --format json|markdown|terminal
```

Exit codes: 0 = PASS/skip-allow, 1 = BLOCK, 2 = error. Hook passes only exit 0.

## 18. Generator / Generated Files

Detect compiled assets, generated API clients, build output, coverage,
vendor/node_modules, lock files. Repo policy per category: `ignore` (default
sane) | `review` | `security_only`.

## 19. Testing Strategy

- `tests/unit`, `tests/integration`, `tests/fixtures` (sample repos per
  language, tmp-built fake git repos, recorded LLM/HTTP responses).
- Network/subprocess boundaries mocked or simulated; real llama.cpp only when
  `AI_REVIEW_INTEGRATION=1`.
- Coverage target ≥85% excluding CLI-only glue (verify with `--cov`).
- `--doctor` doubles as a live integration probe.
- Each phase ships tests; a phase lands only with its tests green.

## 20. Distribution (D3)

- `pyproject.toml` (hatchling or setuptools; decision in plan), console script
  `ai-review`, package `ai_review` ≥3.10, typed.
- Publish to PyPI / private index; org installs `pipx install ai-review` or
  `uv tool install ai-review`. Hook wrapper invokes the installed executable via
  PATH (with explicit fallback documentation when not on PATH).
- `pip install -e .` supported for dev.

## 21. Phased Implementation Roadmap

Each phase = working, tested, usable, committable. No advanced subsystem is
implemented before its phase.

### Phase 1 — MVP vertical slice (replaces bare pre-commit hook)
- Packaging (pyproject, entry point, `--version/--help`), safe-subprocess git
  collector (all file-status cases), evidence-based detector + profile,
  classifier, security redactor + bundled secret scan, provider factory
  (llama.cpp + OpenAI-compatible), grammar/JSON-mode capability, parser +
  corrective retry, validator, policy engine, output (terminal/JSON/Markdown),
  failure policy, layered config (org/user/repo/CLI) with org enforcement,
  `--dry-run`, `--doctor`, hook install/uninstall.

### Phase 2 — Context engine (§11, §12)
  Surrounding lines, containing function/module, imports/dependencies, limits
  (`context.lines_before/after`, `max_file_size_kb`, `max_total_context_kb`).

### Phase 3 — Deterministic check runner (§13, §14)
  Auto-detect tools, trusted/untrusted command separation, CheckResult
  wiring into review output.

### Phase 4 — Technology analyzers (§9)
  PHP, JavaScript, TypeScript, Python, Go, Java, SQL, Docker, Terraform,
  Kubernetes prompt plugins + file-classification depth per technology.

### Phase 5 — Large-change chunking (§24)
  Logical grouping + parallel chunk review via httpx async; aggregate findings.

### Phase 6 — Validation reviewer, caching, generated files, extended doctor
  (§21, §30, §31, §33)

Phases 2 and 3 are independent and may interleave. Phase 5 depends on provider
async readiness (kept async-friendly from Phase 1). Phase 6 latches onto all.

## 22. Success Criteria

1. `git commit` in an instrumented repo runs the hook, review completes, and
   no source leaves a local-first developer machine.
2. A known-bad staged change (nil deref, hardcoded secret) yields a BLOCK with a
   validated, evidence-backed finding; a clean change yields PASS quickly.
3. Different stacks (PHP vs Go vs TS monorepo) get the correct prompt layers and
   deterministic checks only for their stack.
4. `--doctor` verifies every subsystem and reports readiness honestly.
5. Full suite is green at ≥85% coverage after every phase.