# AGENTS.md

## Project
`ai-review` — technology/provider-agnostic AI Git pre-commit review agent. Python >= 3.10, `src/` layout, hatchling build. Console script `ai-review` → `ai_review.cli:main`. Deps: httpx, pydantic, PyYAML.

## Commands
- Run all tests (fully offline — no LLM server or network needed): `.venv/bin/python -m pytest` (333 passing)
- Single test: `.venv/bin/python -m pytest tests/unit/test_config.py -k <name>`
- Coverage: `.venv/bin/python -m pytest --cov=ai_review`
- Manual smoke: `.venv/bin/ai-review --dry-run` (never touches the provider) or `.venv/bin/ai-review --doctor`
- No linter/formatter/typecheck is configured — don't invent one.

## Architecture
`src/ai_review/pipeline.py` is the composition root. Review order: collect staged → `detect()`/`profile_from_names()` (technology → profile) → `classify(data)` → unified diff → `redact_text()` → security `scan_staged()` → resolution gate `scan_unresolved()` → prompt build → `ReviewSession.run()` (the LLM call) → `validate_findings()` → `PolicyEngine.decide()`.

Invariants that break if you reorder code:
- `Pipeline.run()` returns `ReviewResult | str` — the dry-run path returns plan text and never invokes the provider.
- Security scan and the resolution gate (`resolution.py`) run **before** the LLM and are hard-blocking. The resolution gate is NOT subject to `policy.block_on` or `minimum_confidence_to_block` — it always blocks on an unbound reference. It reads the staged files from the working tree, not the diff, so diff truncation can't hide a missing import. It only flags names defined somewhere in the repo (framework global aliases are never false positives) and resolves in favor of "bound" when unsure.
- Config layering (highest wins): defaults `config/default.yaml` < org `AI_REVIEW_ORG_CONFIG` < user `~/.config/ai-review/config.yaml` < repo `.ai-review.yaml` < CLI `--config key=value`. Rules under org `organization.enforce` cannot be weakened by lower layers.
- CLI contract: every handled error prints `error: ...` to stderr and exits 2 — never a traceback, and a pre-commit hook must never see one. Exit 0 = pass/warn, 1 = blocked, 2 = error. `--doctor` reuses 1 for "not ready". The pre-commit hook fails open.

## Packaging quirk
Prompt assets (`prompts/*.md`, `prompts/technology/*.md`) and `config/default.yaml` ship as package data via hatch and are read relative to `__file__` (see `default_prompt_dir()`); `tests/unit/test_package_data.py` guards their presence. Any new asset must live under `src/ai_review/prompts/` or `src/ai_review/config/` and stay covered by that test.

## Conventions
- Commit messages use conventional prefixes (`feat:`, `fix:`, `docs:`, ...).
- Tests: `tests/unit/` is hermetic; `tests/integration/test_pipeline.py` builds real git repos under `tmp_path` (init + set user.name/email) and fakes the LLM with `StaticProvider` / `RecordingProvider` / `DeadProvider`. Never require a live llama.cpp/openai endpoint.
- The LLM answer alone never decides blocking: `validate_findings` re-checks findings against the actual changes, and offline-deterministic findings (secrets, unresolved references) are prepended before the policy gate.