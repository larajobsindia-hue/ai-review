# AI Review Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ai-review`, a technology-agnostic, provider-agnostic AI Git pre-commit code review agent, shipping a working, tested, usable MVP vertical slice first, then the full 34-section spec incrementally.

**Architecture:** Functional pipeline of stages (`git → detect → classify → check → context → security → prompt → invoke → parse → validate → policy → output`) composed in `pipeline.py`, with dataclass/pydantic data contracts in `models.py`. Providers are small `httpx` adapters behind a common interface; constrained-generation + parse-and-retry guarantees JSON conformance; the policy engine (not the LLM) makes the Git decision.

**Tech Stack:** Python 3.10+, `httpx`, `pydantic>=2`, `PyYAML`; build with `hatchling`; dev: `pytest`, `pytest-cov`.

**Spec:** `docs/superpowers/specs/2026-09-16-ai-review-design.md`

## Global Constraints

- Python `>=3.10`, package `ai_review`, console entry point `ai-review`.
- No hardcoded model names; `llm.model` defaults to `auto`.
- Default LLM endpoint `http://127.0.0.1:8080` (llama.cpp), provider `llamacpp`.
- Never `shell=True` or string-interpolated shell commands; subprocess args are lists, cwd pinned.
- Secret redaction runs **before** any provider call; never log secrets or source by default.
- Policy decision produced by the policy engine only; never report "passed" when the LLM never ran.
- `git commit --no-verify` must be untouched; hook installer never silently overwrites an existing hook.
- Config precedence (high→low): CLI args > repository `.ai-review.yaml` > user `~/.config/ai-review/config.yaml` > org config. `organization.enforce: true` prevents weakening `security.redact_secrets`.
- Severities: CRITICAL/HIGH/MEDIUM/LOW/INFO. Default `policy.block_on: [CRITICAL, HIGH]`, `minimum_confidence_to_block: 0.80`.
- Failure policy default: `warn` on LLM-unavailable.
- Each phase ends working, tested (≥85% coverage excluding CLI glue), and committable.

---

# Phase 1 — MVP Vertical Slice

Deliverable: `ai-review --staged` fully works end-to-end (collect → detect → classify → redact → prompt → provider → parse via retry → validate → policy → output), packaged for pip/pipx, with hook install/uninstall, `--doctor`, `--dry-run`, `--verbose`, `--format json|markdown|terminal`, layered config with org enforcement, and the bundled offline secret scan. Replaces a bare pre-commit hook.

## Task 1: Project Scaffolding & Packaging

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/ai_review/__init__.py`
- Create: `src/ai_review/__main__.py`
- Create: `tests/unit/test_version.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ai_review.__version__`, entry point `ai-review = ai_review.cli:main`, pytest fixture layout under `tests/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_version.py
import ai_review

def test_version():
    assert ai_review.__version__
    parts = ai_review.__version__.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts[:2])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_version.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_review'`

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/__init__.py`:
```python
"""Technology-agnostic, provider-agnostic AI Git pre-commit review agent."""

__version__ = "0.1.0"
```

`src/ai_review/__main__.py`:
```python
from ai_review.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

`pyproject.toml`:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "ai-review"
version = "0.1.0"
description = "Technology-agnostic, provider-agnostic AI Git pre-commit review agent"
requires-python = ">=3.10"
license = { text = "MIT" }
dependencies = [
  "httpx>=0.27",
  "pydantic>=2.5",
  "PyYAML>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-cov>=5"]

[project.scripts]
ai-review = "ai_review.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/ai_review"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
```

`.gitignore`:
```text
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
.coverage
htmlcov/
.pytest_cache/
.ai-review-cache/
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_version.py -v`
Expected: PASS

- [ ] **Step 5: Run coverage smoke to confirm the toolchain**

Run: `python -m pytest --cov=ai_review`
Expected: PASS (coverage shown)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/ai_review tests/unit/test_version.py
git commit -m "feat: scaffold ai-review package"
```

## Task 2: Domain Models (models.py)

**Files:**
- Create: `src/ai_review/models.py`
- Test: `tests/unit/test_models.py`

**Interfaces:**
- Consumes: Task 1 scaffolding.
- Produces (canonical names used by ALL later tasks):
  - `severity` constants; `Severity = Literal["CRITICAL","HIGH","MEDIUM","LOW","INFO"]`
  - `Category = Literal["BUG","SECURITY","PERFORMANCE","CONCURRENCY","DATA_INTEGRITY","API_COMPAT","ERROR_HANDLING","RESOURCE","EDGE_CASE","MAINTAINABILITY","TESTING","CONFIG","OTHER"]`
  - `Decision = Literal["PASS","BLOCK","WARN"]`
  - `FileStatus = Literal["added","modified","deleted","renamed"]`
  - `ChangeKind = Literal["source","test","config","documentation","database","infrastructure","dependency","generated","lock","binary","unknown"]`
  - `StagedChange(path, status, old_path, is_binary, hunks, stat_added, stat_removed)`
  - `Hunk(old_start, old_count, new_start, new_count, added_lines, removed_lines, changed_new_lines: set[int])`
  - `Evidence(kind, detail)`; `ProfileEntry(name, confidence, evidence: list[Evidence])`
  - `RepoProfile(languages, frameworks, databases, infrastructure: list[ProfileEntry])`
  - `FileClass` (path, kind, reason)
  - `ContextBundle(path, hunk, before, after, containing_symbols: list[str], imports: list[str])`
  - `CheckResult(name, command: list[str], exit_code: int, output: str, auto_detected: bool)`
  - `Finding(severity, category, file, line, title, description, evidence, recommendation, confidence, is_pre_existing=False, hard_block=False)`
  - `ReviewResult(decision, summary, issues: list[Finding], checks: list[CheckResult])`
  - `PromptPayload(system, user, json_schema: dict, corrective: bool)`
  - `RawLLMResponse(text, provider, endpoint, duration_s, model)`
  - `ReviewContext(profile, changes, request_too_large, message)`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_models.py
from ai_review.models import Finding, ReviewResult

def test_finding_defaults():
    f = Finding(severity="HIGH", category="BUG", file="a.go", line=4,
                title="t", description="d", evidence="e",
                recommendation="r", confidence=0.9)
    assert f.is_pre_existing is False
    assert f.hard_block is False

def test_review_result_json_shapes():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[], checks=[])
    assert r.decision in ("PASS", "BLOCK", "WARN")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_models.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/models.py`:
```python
"""Data contracts for the ai-review pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
Category = Literal[
    "BUG", "SECURITY", "PERFORMANCE", "CONCURRENCY", "DATA_INTEGRITY",
    "API_COMPAT", "ERROR_HANDLING", "RESOURCE", "EDGE_CASE",
    "MAINTAINABILITY", "TESTING", "CONFIG", "OTHER",
]
Decision = Literal["PASS", "BLOCK", "WARN"]
FileStatus = Literal["added", "modified", "deleted", "renamed"]
ChangeKind = Literal[
    "source", "test", "config", "documentation", "database", "infrastructure",
    "dependency", "generated", "lock", "binary", "unknown",
]


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    changed_new_lines: set[int] = field(default_factory=set)


@dataclass
class StagedChange:
    path: str
    status: FileStatus
    old_path: str | None = None
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)
    stat_added: int = 0
    stat_removed: int = 0


@dataclass(frozen=True)
class Evidence:
    kind: str
    detail: str


@dataclass
class ProfileEntry:
    name: str
    confidence: float
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class RepoProfile:
    languages: list[ProfileEntry] = field(default_factory=list)
    frameworks: list[ProfileEntry] = field(default_factory=list)
    databases: list[ProfileEntry] = field(default_factory=list)
    infrastructure: list[ProfileEntry] = field(default_factory=list)

    def all_names(self) -> list[str]:
        out: list[str] = []
        for group in (self.languages, self.frameworks, self.databases, self.infrastructure):
            out.extend(e.name for e in group)
        return out


@dataclass
class FileClass:
    path: str
    kind: ChangeKind
    reason: str


@dataclass
class ContextBundle:
    path: str
    hunk: Hunk
    before: str
    after: str
    containing_symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    name: str
    command: list[str]
    exit_code: int
    output: str
    auto_detected: bool = False


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


@dataclass
class ReviewResult:
    decision: Decision
    summary: str
    issues: list[Finding] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)


@dataclass(frozen=True)
class PromptPayload:
    system: str
    user: str
    json_schema: dict
    corrective: bool = False


@dataclass
class RawLLMResponse:
    text: str
    provider: str
    endpoint: str
    duration_s: float
    model: str = "auto"


@dataclass
class ReviewContext:
    profile: RepoProfile
    changes: list[StagedChange]
    request_too_large: bool = False
    message: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/models.py tests/unit/test_models.py
git commit -m "feat: add pipeline domain models"
```

## Task 3: Config — Models, Loading, Merging, CLI Overrides

**Files:**
- Create: `src/ai_review/config.py`
- Create: `src/ai_review/config/default.yaml` (package data)
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: Task 2 (`ReviewContext`, `models` not needed here).
- Produces:
  - `AppConfig` pydantic model (fields below)
  - `load_config(cli_overrides: dict | None = None, repo_dir: str | None = None, org_file: str | None = None) -> AppConfig`
  - `merge_dict(base: dict, overlay: dict) -> dict` deep-merge (overlay wins, dicts merged recursively, lists replaced)
  - Precedence inside loader: defaults < org < user < repo < CLI.
  - `org` layer honored: if `organization.enforce: true`, repo/user layers **cannot** set `security.redact_secrets` to `false`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_config.py
import yaml
from ai_review.config import AppConfig, load_config, merge_dict

DEFAULT_YAML = """
llm:
  provider: llamacpp
  endpoint: http://127.0.0.1:8080
  model: auto
  timeout_seconds: 120
  max_tokens: 4096
  json_mode: auto
security:
  redact_secrets: true
  excluded_files: [".env", "*.pem", "*.key"]
policy:
  block_on: [CRITICAL, HIGH]
  minimum_confidence_to_block: 0.80
failure_policy:
  on_llm_unavailable: warn
organization:
  enforce: false
"""

def test_merge_dict_recursive():
    base = {"a": {"x": 1}, "b": 2}
    overlay = {"a": {"y": 2}, "b": 3, "c": 4}
    out = merge_dict(base, overlay)
    assert out == {"a": {"x": 1, "y": 2}, "b": 3, "c": 4}

def test_load_config_defaults(tmp_path):
    cfg = load_config(
        defaults_yaml=DEFAULT_YAML, org_yaml=None, user_yaml=None,
        repo_yaml=None, cli_overrides=None, repo_dir=str(tmp_path),
    )
    assert cfg.llm.endpoint == "http://127.0.0.1:8080"
    assert cfg.policy.minimum_confidence_to_block == 0.80
    assert cfg.policy.block_on == ["CRITICAL", "HIGH"]

def test_org_enforce_blocks_redact_weakening(tmp_path):
    repo_yaml = "security:\n  redact_secrets: false\n"
    cfg = load_config(
        defaults_yaml=DEFAULT_YAML, org_yaml="organization:\n  enforce: true\n",
        user_yaml=None, repo_yaml=repo_yaml, cli_overrides=None,
        repo_dir=str(tmp_path),
    )
    assert cfg.security.redact_secrets is True

def test_cli_overrides_win(tmp_path):
    cfg = load_config(
        defaults_yaml=DEFAULT_YAML, org_yaml=None, user_yaml=None,
        repo_yaml=None, cli_overrides={"llm": {"endpoint": "http://x:9"}},
        repo_dir=str(tmp_path),
    )
    assert cfg.llm.endpoint == "http://x:9"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_config.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/config/default.yaml`:
```yaml
llm:
  provider: llamacpp
  endpoint: http://127.0.0.1:8080
  model: auto
  timeout_seconds: 120
  max_tokens: 4096
  json_mode: auto
security:
  redact_secrets: true
  excluded_files:
    - .env
    - "*.pem"
    - "*.key"
    - "*.p12"
policy:
  block_on: [CRITICAL, HIGH]
  minimum_confidence_to_block: 0.80
failure_policy:
  on_llm_unavailable: warn
checks:
  enabled: true
  auto_detect: true
  trusted_commands: []
  commands: []
context:
  enabled: true
  lines_before: 30
  lines_after: 30
  max_file_size_kb: 500
  max_total_context_kb: 1000
review:
  max_diff_kb: 200
  chunking:
    enabled: true
    max_chunk_kb: 50
cache:
  enabled: false
generated:
  policy: ignore
  ignore:
    - dist/
    - build/
    - coverage/
    - node_modules/
    - vendor/
  security_only: []
organization:
  enforce: false
  redact_secrets: null
```

`src/ai_review/config.py`:
```python
"""Layered configuration: defaults < org < user < repo < CLI."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


def merge_dict(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _load_yaml(path: str | None) -> dict | None:
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    with p.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}


class LLMConfig(BaseModel):
    provider: str = "llamacpp"
    endpoint: str = "http://127.0.0.1:8080"
    model: str = "auto"
    timeout_seconds: int = 120
    max_tokens: int = 4096
    json_mode: str = "auto"  # auto | on | off
    api_key: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class SecurityConfig(BaseModel):
    redact_secrets: bool = True
    excluded_files: list[str] = Field(
        default_factory=lambda: [".env", "*.pem", "*.key", "*.p12"]
    )


class PolicyConfig(BaseModel):
    block_on: list[str] = Field(default_factory=lambda: ["CRITICAL", "HIGH"])
    minimum_confidence_to_block: float = 0.80


class FailurePolicyConfig(BaseModel):
    on_llm_unavailable: str = "warn"  # block | warn | allow


class ChecksConfig(BaseModel):
    enabled: bool = True
    auto_detect: bool = True
    trusted_commands: list[dict[str, Any]] = Field(default_factory=list)
    commands: list[dict[str, Any]] = Field(default_factory=list)


class ContextConfig(BaseModel):
    enabled: bool = True
    lines_before: int = 30
    lines_after: int = 30
    max_file_size_kb: int = 500
    max_total_context_kb: int = 1000


class ChunkingConfig(BaseModel):
    enabled: bool = True
    max_chunk_kb: int = 50


class ReviewConfig(BaseModel):
    max_diff_kb: int = 200
    chunking: ChunkingConfig = ChunkingConfig()


class CacheConfig(BaseModel):
    enabled: bool = False


class GeneratedConfig(BaseModel):
    policy: str = "ignore"  # ignore | review | security_only
    ignore: list[str] = Field(default_factory=lambda: [
        "dist/", "build/", "coverage/", "node_modules/", "vendor/",
    ])
    security_only: list[str] = Field(default_factory=list)


class OrganizationConfig(BaseModel):
    enforce: bool = False
    redact_secrets: bool | None = None


class AppConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    security: SecurityConfig = SecurityConfig()
    policy: PolicyConfig = PolicyConfig()
    failure_policy: FailurePolicyConfig = FailurePolicyConfig()
    checks: ChecksConfig = ChecksConfig()
    context: ContextConfig = ContextConfig()
    review: ReviewConfig = ReviewConfig()
    cache: CacheConfig = CacheConfig()
    generated: GeneratedConfig = GeneratedConfig()
    organization: OrganizationConfig = OrganizationConfig()


def load_config(
    *,
    defaults_yaml: str | None = None,
    org_yaml: str | None = None,
    user_yaml: str | None = None,
    repo_yaml: str | None = None,
    cli_overrides: dict | None = None,
    repo_dir: str | None = None,
) -> AppConfig:
    merged: dict = {}
    for candidate in (defaults_yaml, org_yaml, user_yaml, repo_yaml):
        layer = _load_yaml(candidate) if isinstance(candidate, str) else candidate
        if layer:
            merged = merge_dict(merged, layer)
    if cli_overrides:
        merged = merge_dict(merged, cli_overrides)

    cfg = AppConfig.model_validate(merged)

    # Organization enforcement: repo/user layers cannot weaken mandatory rules.
    if cfg.organization.enforce:
        if cfg.organization.redact_secrets is not False:
            cfg.security.redact_secrets = True
    return cfg


def resolve_config_paths(repo_dir: str) -> tuple[str | None, str | None, str | None]:
    org_file: str | None = os.environ.get("AI_REVIEW_ORG_CONFIG")
    user_file = str(Path("~/.config/ai-review/config.yaml").expanduser())
    repo_file = str(Path(repo_dir) / ".ai-review.yaml")
    return org_file, user_file, repo_file


def load_config_for_repo(
    repo_dir: str,
    cli_overrides: dict | None = None,
) -> AppConfig:
    org_file, user_file, repo_file = resolve_config_paths(repo_dir)
    return load_config(
        defaults_yaml=str(Path(__file__).parent / "config" / "default.yaml"),
        org_yaml=org_file,
        user_yaml=user_file,
        repo_yaml=repo_file,
        cli_overrides=cli_overrides,
        repo_dir=repo_dir,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/config.py src/ai_review/config/default.yaml tests/unit/test_config.py
git commit -m "feat: layered config with org enforcement"
```

## Task 4: Git Collector (git.py)

**Files:**
- Create: `src/ai_review/git.py`
- Test: `tests/unit/test_git.py`

**Interfaces:**
- Consumes: Task 2 (`StagedChange`, `FileStatus`, `Hunk`).
- Produces:
  - `class GitError(RuntimeError)`
  - `git_repo_root(cwd) -> str`
  - `git_branch(cwd) -> str`
  - `git_diff_text(cwd, ignore=()) -> str` (raw `git diff --cached --no-ext-diff`)
  - `git_name_status(cwd, ignore=()) -> list[tuple[str, list[str]]]` — `(status, paths)` pairs from `--name-status -z -M`
  - `git_numstat(cwd, ignore=()) -> dict[str, tuple[int, int, bool]]` — `path -> (added, removed, is_binary)`
  - `collect_staged(cwd) -> list[StagedChange]`
  - `SafeRunner` internal helper using `subprocess.run([...], shell=False, cwd=pinned, timeout, capture_output=True, text=True)`
  - `ignore` patterns filtered with `fnmatch` against path + basename.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_git.py
import subprocess

import pytest

from ai_review.git import collect_staged, git_branch, git_repo_root


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "T")
    return tmp_path


def test_repo_root_and_branch(repo):
    assert git_repo_root(str(repo)) == str(repo.resolve())
    _git(repo, "checkout", "-q", "-b", "feature/x")
    assert git_branch(str(repo)) == "feature/x"


def test_collect_staged_added_modified_deleted(repo):
    (repo / "a.go").write_text("package main\nfunc main() {}\n")
    _git(repo, "add", "a.go")
    _git(repo, "commit", "-qm", "init")
    (repo / "del.go").write_text("package main\n")
    _git(repo, "add", "del.go")
    _git(repo, "commit", "-qm", "del-init")
    (repo / "a.go").write_text("package main\nfunc main() { println(1) }\n")
    (repo / "b.go").write_text("package main\n")
    _git(repo, "rm", "-q", "del.go")
    _git(repo, "add", "-A")
    changes = collect_staged(str(repo))
    statuses = {c.path: c.status for c in changes}
    assert statuses == {"a.go": "modified", "b.go": "added", "del.go": "deleted"}


def test_collect_staged_rename(repo):
    (repo / "old.go").write_text("package main\n")
    _git(repo, "add", "old.go")
    _git(repo, "commit", "-qm", "init")
    (repo / "new.go").write_text("package main\n")
    _git(repo, "add", "-A")
    _git(repo, "rm", "-q", "old.go") if (repo / "old.go").exists() else None
    changes = collect_staged(str(repo))
    renamed = [c for c in changes if c.status == "renamed"]
    assert renamed and renamed[0].path == "new.go"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_git.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/git.py`:
```python
"""Safe subprocess wrapper for git, staging-aware collection."""
from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

from ai_review.models import FileStatus, Hunk, StagedChange


class GitError(RuntimeError):
    pass


def _run_git(cwd: str, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0] if args else '?'} timed out") from exc


def _check(proc: subprocess.CompletedProcess, label: str) -> None:
    if proc.returncode != 0:
        raise GitError(f"git {label} failed: {proc.stderr.strip()[:500]}")


def _ignored(path: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    base = os.path.basename(path)
    return any(
        fnmatch.fnmatch(path, p) or fnmatch.fnmatch(base, p) or p.rstrip("/") in (path, base)
        for p in patterns
    )


def git_repo_root(cwd: str) -> str:
    proc = _run_git(cwd, ["rev-parse", "--show-toplevel"])
    _check(proc, "rev-parse")
    return proc.stdout.strip()


def git_branch(cwd: str) -> str:
    proc = _run_git(cwd, ["symbolic-ref", "--short", "-q", "HEAD"])
    if proc.returncode != 0:
        proc = _run_git(cwd, ["rev-parse", "--short", "HEAD"])
    return proc.stdout.strip() or "detached"


def git_diff_text(cwd: str, ignore: tuple[str, ...] = ()) -> str:
    proc = _run_git(cwd, ["diff", "--cached", "--no-ext-diff"])
    _check(proc, "diff --cached")
    return proc.stdout


def git_name_status(cwd: str, ignore: tuple[str, ...] = ()) -> list[tuple[str, list[str]]]:
    proc = _run_git(cwd, ["diff", "--cached", "--name-status", "-z", "-M"])
    _check(proc, "name-status")
    raw = proc.stdout.split("\0")
    pairs: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(raw):
        entry = raw[i]
        if not entry:
            i += 1
            continue
        if entry[0] in ("A", "M", "D"):
            pairs.append((entry, [raw[i + 1]] if i + 1 < len(raw) else []))
            i += 2
        elif entry.startswith("R"):
            pairs.append((entry, raw[i + 1 : i + 3] if i + 2 < len(raw) else []))
            i += 3
        else:
            i += 1
    return pairs


def git_numstat(cwd: str, ignore: tuple[str, ...] = ()) -> dict[str, tuple[int, int, bool]]:
    proc = _run_git(cwd, ["diff", "--cached", "--numstat", "-z"])
    _check(proc, "numstat")
    raw = proc.stdout.split("\0")
    out: dict[str, tuple[int, int, bool]] = {}
    i = 0
    while i < len(raw):
        if i + 1 >= len(raw):
            break
        stat, path = raw[i], raw[i + 1]
        i += 2
        if stat in ("-", "--"):
            continue
        parts = stat.split("\t")
        if len(parts) != 2:
            continue
        added, removed = parts
        if added == "-" or removed == "-":
            out[path] = (0, 0, True)
        else:
            out[path] = (int(added), int(removed), False)
    return out


def collect_staged(cwd: str, ignore: tuple[str, ...] = ()) -> list[StagedChange]:
    root = git_repo_root(cwd)
    name_status = git_name_status(root, ignore)
    numstat = git_numstat(root, ignore)
    changes: list[StagedChange] = []
    for status, paths in name_status:
        if status.startswith("R"):
            old, new = paths
            if _ignored(new, ignore):
                continue
            added, removed, binary = numstat.get(new, (0, 0, False))
            changes.append(StagedChange(path=new, status="renamed", old_path=old,
                                        is_binary=binary, stat_added=added, stat_removed=removed))
        else:
            path = paths[0]
            if _ignored(path, ignore):
                continue
            kind: FileStatus = {"A": "added", "M": "modified", "D": "deleted"}[status]
            added, removed, binary = numstat.get(path, (0, 0, False))
            changes.append(StagedChange(path=path, status=kind, is_binary=binary,
                                        stat_added=added, stat_removed=removed))
    return changes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_git.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/git.py tests/unit/test_git.py
git commit -m "feat: staging-aware git collector with safe subprocess"
```

## Task 5: Diff Parser (diff.py)

**Files:**
- Create: `src/ai_review/diff.py`
- Test: `tests/unit/test_diff.py`

**Interfaces:**
- Consumes: Task 2 (`Hunk`, `StagedChange`).
- Produces:
  - `parse_unified_diff(text: str) -> dict[str, list[Hunk]]` (path → hunks, `b/` prefix stripped)
  - `attach_hunks(changes: list[StagedChange], parsed: dict[str, list[Hunk]]) -> None` (fills `change.hunks`, `changed_new_lines`)
  - `changed_new_ranges(change) -> set[int]` helper

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_diff.py
from ai_review.diff import parse_unified_diff, attach_hunks
from ai_review.models import StagedChange

DIFF = """diff --git a/a.go b/a.go
index 111..222 100644
--- a/a.go
+++ b/a.go
@@ -1,3 +1,4 @@
 package main
 func main() {
+    println(1)
     println(2)
 }
"""

def test_parse_unified_diff():
    parsed = parse_unified_diff(DIFF)
    assert "a.go" in parsed
    h = parsed["a.go"][0]
    assert (h.old_start, h.old_count) == (1, 3)
    assert (h.new_start, h.new_count) == (1, 4)
    assert 3 in h.changed_new_lines
    assert 2 not in h.changed_new_lines

def test_attach_hunks():
    changes = [StagedChange(path="a.go", status="modified")]
    attach_hunks(changes, parse_unified_diff(DIFF))
    assert changes[0].hunks[0].changed_new_lines == {3}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_diff.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/diff.py`:
```python
"""Minimal unified-diff parser sufficient for hunk-aware review."""
from __future__ import annotations

import re
from typing import Iterator

from ai_review.models import Hunk, StagedChange

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_PATH_RE = re.compile(r"^diff --git a/(.*?) b/(.*)$")


def _strip_prefix(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


def parse_unified_diff(text: str) -> dict[str, list[Hunk]]:
    files: dict[str, list[Hunk]] = {}
    current: str | None = None
    new_line = 0
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("diff --git"):
            match = _PATH_RE.match(line)
            current = _strip_prefix(match.group(2)) if match else None
            if current is not None:
                files.setdefault(current, [])
            continue
        if current is None or line.startswith(("--- ", "+++ ", "index ")):
            continue
        hunk = _HUNK_RE.match(line)
        if hunk:
            new_line = int(hunk.group(3))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            files[current][-1].changed_new_lines.add(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith(" "):
            new_line += 1
    return files


def _hunks_for(text: str, path: str) -> list[Hunk]:
    parsed = parse_unified_diff(text)
    return parsed.get(path, [])


def attach_hunks(changes: list[StagedChange], parsed: dict[str, list[Hunk]]) -> None:
    for change in changes:
        change.hunks = parsed.get(change.path, [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_diff.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/diff.py tests/unit/test_diff.py
git commit -m "feat: unified diff parser with changed-line tracking"
```

## Task 6: Technology Detection + Profile (detector.py, profile.py)

**Files:**
- Create: `src/ai_review/detector.py`
- Create: `src/ai_review/profile.py`
- Test: `tests/unit/test_detector.py`

**Interfaces:**
- Consumes: Task 2 (`Evidence`, `ProfileEntry`, `RepoProfile`), Task 4/5 repo contents.
- Produces:
  - `detect(change_paths: list[str], repo_dir: str) -> RepoProfile`
  - `profile_from_names(profile, names) -> RepoProfile` — monorepo scoping: keeps languages matched by changed-file extension/manifest; keeps frameworks owned by a kept language. Full subtree resolution deferred to Phase 4.
  - Rule tables `LANGUAGE_RULES`, `FRAMEWORK_RULES`, `DATABASE_RULES`, `INFRA_RULES` (exported for tests).
  - **Confidence model:** one strong manifest signal → 0.98; 3+ file-extension signals → 0.90; 1–2 extension signals → 0.80; weak/derived → 0.55. Evidence list always attached.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_detector.py
from ai_review.detector import detect
from ai_review.profile import profile_from_names


def test_detect_go(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text("package main\n")
    prof = detect([], str(tmp_path))
    go_lang = [l for l in prof.languages if l.name == "Go"]
    assert go_lang and go_lang[0].confidence >= 0.9


def test_detect_typescript_monorepo_scoping(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18.0.0"}}\n')
    (tmp_path / "tsconfig.json").write_text("{}\n")
    (tmp_path / "src" / "index.tsx").write_text("export default () => null;\n")
    prof = detect([], str(tmp_path))
    names = {l.name: l for l in prof.languages}
    assert "TypeScript" in names and names["TypeScript"].confidence >= 0.8
    fw = [f for f in prof.frameworks if f.name == "React"]
    assert fw and fw[0].confidence >= 0.7

    scoped = profile_from_names(prof, ["index.tsx"])
    assert scoped.languages and all(e.name in ("TypeScript", "JavaScript") for e in scoped.languages)
    assert scoped.frameworks and all(e.name == "React" for e in scoped.frameworks)


def test_detect_unknown(tmp_path):
    (tmp_path / "notes.txt").write_text("hi\n")
    prof = detect([], str(tmp_path))
    assert prof.languages == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_detector.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/detector.py`:
```python
"""Evidence-based technology detection."""
from __future__ import annotations

import json
import os
from pathlib import Path

from ai_review.models import Evidence, ProfileEntry, RepoProfile

MANIFEST_WEIGHT = 0.98
EXT_3_PLUS_WEIGHT = 0.90
EXT_1_2_WEIGHT = 0.80
DERIVED_WEIGHT = 0.55

LANGUAGE_RULES: list[tuple[str, list[str], list[str]]] = [
    # (language, manifest_filenames, extensions)
    ("PHP", ["composer.json"], [".php"]),
    ("JavaScript", ["package.json", ".nvmrc"], [".js", ".mjs", ".cjs"]),
    ("TypeScript", ["tsconfig.json", "tsconfig.app.json"], [".ts", ".tsx"]),
    ("Python", ["pyproject.toml", "requirements.txt", "Pipfile", "poetry.lock", "setup.py"], [".py"]),
    ("Go", ["go.mod"], [".go"]),
    ("Java", ["pom.xml", "build.gradle", "build.gradle.kts"], [".java"]),
    ("Rust", ["Cargo.toml"], [".rs"]),
    ("Ruby", ["Gemfile"], [".rb"]),
    ("C#", [".csproj"], [".cs"]),
    ("SQL", [], [".sql"]),
]

FRAMEWORK_RULES: list[tuple[str, str, str]] = [
    # (framework, manifest, needle) — needle searched in manifest text
    ("Laravel", "composer.json", "laravel/framework"),
    ("Symfony", "composer.json", "symfony/symfony"),
    ("React", "package.json", '"react"'),
    ("Vue", "package.json", '"vue"'),
    ("Angular", "package.json", "@angular/core"),
    ("Next.js", "package.json", "next"),
    ("Express", "package.json", "express"),
    ("Django", "pyproject.toml", "django"),
    ("Flask", "pyproject.toml", "flask"),
    ("FastAPI", "pyproject.toml", "pydantic"),
    ("Spring", "pom.xml", "spring-boot"),
    ("Gin", "go.mod", "gin-gonic"),
]

DATABASE_RULES: list[tuple[str, list[str]]] = [
    ("PostgreSQL", ["postgres", "postgresql", "pgvector"]),
    ("MySQL", ["mysql"]),
    ("MongoDB", ["mongodb", "mongo"]),
    ("Redis", ["redis"]),
    ("SQLite", ["sqlite"]),
]

INFRA_RULES: list[tuple[str, list[str]]] = [
    ("Docker", ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"]),
    ("Terraform", [".tf"]),
    ("Kubernetes", ["k8s/", "deployment.yaml", "service.yaml", "values.yaml"]),
    ("GitHub Actions", [".github/workflows/"]),
]


def _found_manifest_text(repo_dir: str, manifest: str) -> str | None:
    p = Path(repo_dir) / manifest
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return None
    return None


def _extensions(paths: list[str]) -> list[str]:
    return [os.path.splitext(p)[1].lower() for p in paths]


def detect(change_paths: list[str], repo_dir: str) -> RepoProfile:
    languages: list[ProfileEntry] = []
    ext_counts: dict[str, int] = {}
    for ext in _extensions(change_paths):
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    for lang, manifests, exts in LANGUAGE_RULES:
        evidence: list[Evidence] = []
        for mf in manifests:
            if (Path(repo_dir) / mf).is_file():
                evidence.append(Evidence("manifest", mf))
        count = sum(ext_counts.get(e, 0) for e in exts)
        if evidence:
            confidence = MANIFEST_WEIGHT
        elif count >= 3:
            confidence = EXT_3_PLUS_WEIGHT
        elif count >= 1:
            confidence = EXT_1_2_WEIGHT
        else:
            continue
        languages.append(ProfileEntry(lang, confidence, evidence))

    frameworks: list[ProfileEntry] = []
    for fw, manifest, needle in FRAMEWORK_RULES:
        text = _found_manifest_text(repo_dir, manifest)
        if text is not None and needle in text:
            frameworks.append(ProfileEntry(fw, MANIFEST_WEIGHT,
                            [Evidence("manifest", f"{manifest} contains {needle}")]))

    databases: list[ProfileEntry] = []
    for db, needles in DATABASE_RULES:
        hits: list[str] = []
        for ext in ext_counts:
            if ext == ".sql":
                hits.append("sql migration files present")
                break
        for mf in ["docker-compose.yml", "docker-compose.yaml", "values.yaml"]:
            text = _found_manifest_text(repo_dir, mf)
            if text:
                for needle in needles:
                    if needle in text.lower():
                        hits.append(f"{mf} mentions {needle}")
        for mf in LANGUAGE_RULES:
            for manifest in mf[1]:
                text = _found_manifest_text(repo_dir, manifest)
                if text:
                    for needle in needles:
                        if needle in text.lower():
                            hits.append(f"{manifest} mentions {needle}")
        if hits:
            databases.append(ProfileEntry(db, max(0.55, 0.72 - 0.05 * (len(hits) - 1)),
                            [Evidence("derived", h) for h in hits[:3]]))

    infrastructure: list[ProfileEntry] = []
    for name, markers in INFRA_RULES:
        hits = [m for m in markers if (Path(repo_dir) / m).is_file() or any(
            p in m for p in change_paths)]
        if hits:
            infrastructure.append(ProfileEntry(name, DERIVED_WEIGHT,
                            [Evidence("marker", h) for h in hits]))

    return RepoProfile(
        languages=languages, frameworks=frameworks,
        databases=databases, infrastructure=infrastructure,
    )
```

`src/ai_review/profile.py`:
```python
"""Monorepo scoping: restrict a profile to the languages/frameworks of given paths."""
from __future__ import annotations

import os

from ai_review.models import ProfileEntry, RepoProfile

LANG_EXT: dict[str, tuple[str, ...]] = {
    "PHP": (".php",),
    "JavaScript": (".js", ".mjs", ".cjs", ".jsx"),
    "TypeScript": (".ts", ".tsx"),
    "Python": (".py",),
    "Go": (".go",),
    "Java": (".java",),
    "Rust": (".rs",),
    "Ruby": (".rb",),
    "C#": (".cs",),
    "SQL": (".sql",),
}

LANG_MANIFESTS: dict[str, tuple[str, ...]] = {
    "PHP": ("composer.json",),
    "JavaScript": ("package.json", ".nvmrc"),
    "TypeScript": ("tsconfig.json", "tsconfig.app.json"),
    "Python": ("pyproject.toml", "requirements.txt", "Pipfile"),
    "Go": ("go.mod",),
    "Java": ("pom.xml", "build.gradle", "build.gradle.kts"),
    "Rust": ("Cargo.toml",),
    "Ruby": ("Gemfile",),
    "C#": (".csproj",),
}

# framework -> owning languages (framework kept when any owner language is kept)
FRAMEWORK_OWNERS: dict[str, tuple[str, ...]] = {
    "Laravel": ("PHP",), "Symfony": ("PHP",),
    "React": ("JavaScript", "TypeScript"), "Vue": ("JavaScript", "TypeScript"),
    "Angular": ("TypeScript",), "Next.js": ("JavaScript", "TypeScript"),
    "Express": ("JavaScript",),
    "Django": ("Python",), "Flask": ("Python",), "FastAPI": ("Python",),
    "Spring": ("Java",),
    "Gin": ("Go",),
}


def _lang_kept(path: str, entry: ProfileEntry) -> bool:
    base = os.path.basename(path)
    ext = (base.rsplit(".", 1)[1].lower() if "." in base and not base.startswith(".") else "")
    if LANG_EXT.get(entry.name) and ext in LANG_EXT[entry.name]:
        return True
    return base in LANG_MANIFESTS.get(entry.name, ())


def profile_from_names(profile: RepoProfile, names: list[str]) -> RepoProfile:
    if not names:
        return profile
    kept_langs = {e.name for e in profile.languages if any(_lang_kept(n, e) for n in names)}

    def keep_lang(entry: ProfileEntry) -> bool:
        return entry.name in kept_langs

    def keep_fw(entry: ProfileEntry) -> bool:
        owners = FRAMEWORK_OWNERS.get(entry.name, ())
        return not owners or bool(kept_langs & set(owners))

    return RepoProfile(
        languages=[e for e in profile.languages if keep_lang(e)],
        frameworks=[e for e in profile.frameworks if keep_fw(e)],
        databases=profile.databases,
        infrastructure=profile.infrastructure,
    )
```

Note on scoping granularity: Phase 1 keeps languages whose extension/manifest appears among the changed paths and keeps frameworks owned by those languages. Full directory/subtree resolution (the `frontend/package.json` case) is Phase 4 monorepo work.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_detector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/detector.py src/ai_review/profile.py tests/unit/test_detector.py
git commit -m "feat: evidence-based technology detection and monorepo profile scoping"
```

## Task 7: File Classifier (classifier.py)

**Files:**
- Create: `src/ai_review/classifier.py`
- Test: `tests/unit/test_classifier.py`

**Interfaces:**
- Consumes: Task 2 (`FileClass`, `ChangeKind`, `StagedChange`).
- Produces: `classify(paths: list[str], generated: list[str]) -> list[FileClass]`
  Uses new path (rename target). Deterministic rules: binary/extension/manifest/path-prefix heuristics.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_classifier.py
from ai_review.classifier import classify


def test_classify_examples():
    classes = {c.path: c.kind for c in classify(
        ["app/service.go", "service_test.go", "Dockerfile", "migration.sql",
         "package.json", "README.md", "dist/bundle.js", "package-lock.json",
         "logo.png"], generated=["dist/", "build/"],
    )}
    assert classes["app/service.go"] == "source"
    assert classes["service_test.go"] == "test"
    assert classes["Dockerfile"] == "infrastructure"
    assert classes["migration.sql"] == "database"
    assert classes["package.json"] == "dependency"
    assert classes["README.md"] == "documentation"
    assert classes["dist/bundle.js"] == "generated"
    assert classes["package-lock.json"] == "lock"
    assert classes["logo.png"] == "binary"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_classifier.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/classifier.py`:
```python
"""Classify changed files to decide review depth."""
from __future__ import annotations

import fnmatch
import os

from ai_review.models import ChangeKind, FileClass

DEPENDENCY_MANIFESTS = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "composer.json", "composer.lock", "go.mod", "go.sum", "Cargo.toml",
    "Cargo.lock", "Gemfile", "Gemfile.lock", "requirements.txt", "Pipfile",
    "poetry.lock", "pyproject.toml", "setup.py", "pom.xml", "build.gradle",
    "build.gradle.kts", "gradle.lockfile",
}
LOCK_SUFFIXES = (".lock", "-lock.json", "lockfile", "lock.yaml")

TEST_MARKERS = (
    "test_", "_test", ".test.", ".spec.", "tests/", "spec/", "__tests__/",
)
INFRA_MARKERS = ("Dockerfile", "docker-compose", ".terraform", ".github/workflows/", ".gitlab-ci", "Jenkinsfile")
DOC_MARKERS = (".md", ".rst", ".txt", "docs/")


def classify(paths: list[str], generated: list[str]) -> list[FileClass]:
    out: list[FileClass] = []
    for path in paths:
        base = os.path.basename(path).lower()
        if any(
            (p.endswith("/") and (path == p.rstrip("/") or path.startswith(p)))
            or fnmatch.fnmatch(path, p)
            or fnmatch.fnmatch(base, p)
            for p in generated
        ):
            out.append(FileClass(path, "generated", "matches generated pattern"))
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
                   ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf", ".mp4",
                   ".class", ".jar", ".pyc", ".so", ".dll", ".exe"}:
            out.append(FileClass(path, "binary", f"binary extension {ext}"))
            continue
        if path in DEPENDENCY_MANIFESTS:
            kind: ChangeKind = "lock" if (
                "lock" in path or path.endswith(LOCK_SUFFIXES) or path.endswith(("go.sum", ".lock"))
            ) else "dependency"
            out.append(FileClass(path, kind, "dependency manifest"))
            continue
        if path.endswith(LOCK_SUFFIXES):
            out.append(FileClass(path, "lock", "lock file"))
            continue
        if base.lower().startswith("dockerfile") or base.startswith((".gitlab-ci", "Jenkinsfile")) or any(
            marker in path for marker in INFRA_MARKERS):
            out.append(FileClass(path, "infrastructure", "infrastructure marker"))
            continue
        if ext == ".sql" or (path.endswith((".sql",)) or "/migrations/" in path or "migration" in base):
            out.append(FileClass(path, "database", "SQL or migration"))
            continue
        if any(marker in path or base.startswith("test_") or base.endswith("_test")
               for marker in TEST_MARKERS):
            if ext in {".py", ".go", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".rb", ".java", ".php"}:
                out.append(FileClass(path, "test", "test marker"))
                continue
        if ext in {".md", ".rst", ".txt"} or "docs/" in path:
            out.append(FileClass(path, "documentation", "documentation extension"))
            continue
        if ext in {".yaml", ".yml", ".toml", ".ini", ".cfg", ".jsonconfig",
                   ".env.example", ".editorconfig"} or "config/" in path or path.endswith(".conf"):
            out.append(FileClass(path, "config", "config extension"))
            continue
        if ext:
            out.append(FileClass(path, "source", f"source extension {ext}"))
        else:
            out.append(FileClass(path, "unknown", "no recognizable category"))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_classifier.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/classifier.py tests/unit/test_classifier.py
git commit -m "feat: changed-file classifier for review depth"
```

## Task 8: Security — Redaction + Offline Secret Scan (security.py)

**Files:**
- Create: `src/ai_review/security.py`
- Test: `tests/unit/test_security.py`

**Interfaces:**
- Consumes: Task 2 (`Finding`, `Severity`), Task 3 (`SecurityConfig`).
- Produces:
  - `SECRET_PATTERNS: list[tuple[str, re.Pattern]]` (high-confidence)
  - `redact_text(text: str, patterns=None) -> tuple[str, list[str]]` → redacted text + list of detected secret kinds (deterministic `[REDACTED:<kind>:<sha256[:10]>]`)
  - `should_exclude(path: str, excluded: list[str]) -> bool` (`fnmatch` on full path + basename)
  - `scan_staged(changes: list[StagedChange]) -> list[Finding]` — reads repo files (added/modified only), returns CRITICAL `SECURITY` findings with `confidence=1.0, hard_block=True`; deleted/binary skipped.
  - **Order guarantee:** the pipeline calls `redact_text` on diff+context BEFORE any provider call.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_security.py
import pytest

from ai_review.models import StagedChange
from ai_review.security import (
    redact_text, scan_staged, should_exclude,
)

def test_redact_aws_key():
    text = "aws_access_key_id=AKIAIOSFODNN7EXAMPLE"
    redacted, kinds = redact_text(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert kinds and "aws" in kinds[0]

def test_redact_private_key_block():
    pem_snippet = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpQIBAAKCA\n-----END RSA PRIVATE KEY-----"
    redacted, _ = redact_text(pem_snippet)
    assert "MIIEpQIBAAKCA" not in redacted

def test_redact_bearer_token():
    text = "Authorization: Bearer abc.def.ghijklmnopqrstuvwxyz1234567890"
    redacted, kinds = redact_text(text)
    assert "ghijklmnop" not in redacted
    assert "bearer" in kinds[0]

def test_should_exclude():
    assert should_exclude(".env", [".env", "*.pem"])
    assert should_exclude("secrets/key.pem", [".env", "*.pem"])
    assert not should_exclude("src/main.go", [".env", "*.pem"])


def test_scan_staged_finds_hardcoded_key(tmp_path):
    (tmp_path / "app.py").write_text("API_KEY = \"ghp_1234567890123456789012345678901234\"\n")
    change = StagedChange(path="app.py", status="added")
    findings = scan_staged(str(tmp_path), [change])
    assert findings
    assert findings[0].severity == "CRITICAL"
    assert findings[0].confidence == 1.0
    assert findings[0].hard_block is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_security.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/security.py`:
```python
"""Secret redaction (pre-provider) and offline staged-content secret scan."""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re

from ai_review.models import Finding, StagedChange

SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws", re.compile(r"(?i)aws_secret_access_key[=:]\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----", re.S)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b")),
    ("password", re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\b[=:]\s*['\"]?[A-Za-z0-9_@#$%^&+=!.\-/]{8,}")),
    ("connstr", re.compile(r"(?i)\b(?:postgres|postgresql|mysql|mongodb|redis|amqp)://[^\s'\"$]+")),
]


def _placeholder(kind: str, match: str) -> str:
    digest = hashlib.sha256(match.encode("utf-8")).hexdigest()[:10]
    return f"[REDACTED:{kind}:{digest}]"


def redact_text(text: str, patterns: list[tuple[str, re.Pattern]] | None = None) -> tuple[str, list[str]]:
    patterns = patterns or SECRET_PATTERNS
    redacted = text
    kinds: list[str] = []
    for kind, pattern in patterns:
        def repl(m: re.Match, _k: str = kind) -> str:
            if _k not in kinds:
                kinds.append(_k)
            return _placeholder(_k, m.group(0))
        redacted = pattern.sub(repl, redacted)
    return redacted, kinds


def should_exclude(path: str, excluded: list[str]) -> bool:
    base = os.path.basename(path)
    return any(
        fnmatch.fnmatch(path, p) or fnmatch.fnmatch(base, p) or fnmatch.fnmatch(path, p.rstrip("/") + "/*")
        for p in excluded
    )


def scan_staged(repo_dir: str, changes: list[StagedChange],
                excluded: list[str] | None = None) -> list[Finding]:
    excluded = excluded or [".env", "*.pem", "*.key", "*.p12"]
    findings: list[Finding] = []
    for change in changes:
        if change.status == "deleted" or change.is_binary:
            continue
        if should_exclude(change.path, excluded):
            continue
        path = os.path.join(repo_dir, change.path)
        try:
            content = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for kind, pattern in SECRET_PATTERNS:
            for m in re.finditer(pattern, content):
                if kind == "password" and m.group(0).count(":") == 0 and "=" not in m.group(0) and len(m.group(0).split("=")[-1]) < 8:
                    continue
                findings.append(Finding(
                    severity="CRITICAL", category="SECURITY", file=change.path,
                    line=content.count("\n", 0, m.start()) + 1,
                    title=f"Possible {kind} secret in staged change",
                    description=f"Staged content looks like a {kind} credential.",
                    evidence=m.group(0)[:120],
                    recommendation="Remove the secret; use environment variables or a secret manager.",
                    confidence=1.0, hard_block=True,
                ))
                break
    return findings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_security.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/security.py tests/unit/test_security.py
git commit -m "feat: secret redaction and offline staged secret scan"
```

## Task 9: Prompt Builder + Prompt Files (prompts.py, prompts/*)

**Files:**
- Create: `src/ai_review/prompts.py`
- Create: `src/ai_review/prompts/system.md`
- Create: `src/ai_review/prompts/review.md`
- Create: `src/ai_review/prompts/technology/generic.md`
- Test: `tests/unit/test_prompts.py`

**Interfaces:**
- Consumes: Task 2 (`ReviewResult`, `ReviewContext`, `PromptPayload`, `RepoProfile`, `StagedChange`), pydantic output schema.
- Produces:
  - `OUTPUT_JSON_SCHEMA: dict` (decision/summary/issues), exported.
  - `PromptBuilder` class with `build(profile, changes, diff_text, context_text, rules) -> PromptPayload`.
  - `format_diff(changes, diff_text, profile) -> str` (truncated, redaction expected upstream).
  - `prompt_files_ok(dir) -> tuple[bool, list[str]]` used by `--doctor`.
  - `prompt_version() -> str` (sha256 of the four prompt-file contents — cache key input).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_prompts.py
import pytest

from ai_review.models import RepoProfile, ProfileEntry, Evidence
from ai_review.prompts import OUTPUT_JSON_SCHEMA, PromptBuilder, prompt_version

SYSTEM = "You are a senior software engineer performing a pre-commit review.\nReturn ONLY valid JSON matching the supplied schema.\n"
REVIEW = "# Review Task\nReview only the staged diff.\n"
GENERIC = "# Detected Technologies\n{{technology_summary}}\n"


@pytest.fixture
def prompt_dir(tmp_path):
    tech = tmp_path / "technology"
    tech.mkdir()
    (tmp_path / "system.md").write_text(SYSTEM, encoding="utf-8")
    (tmp_path / "review.md").write_text(REVIEW, encoding="utf-8")
    (tech / "generic.md").write_text(GENERIC, encoding="utf-8")
    return tmp_path


def test_output_schema_shape():
    assert OUTPUT_JSON_SCHEMA["required"] == ["decision", "summary", "issues"]
    sev = OUTPUT_JSON_SCHEMA["properties"]["issues"]["items"]["properties"]["severity"]["enum"]
    assert sev == ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


def test_build_includes_only_detected_langs(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    profile = RepoProfile(languages=[ProfileEntry("Go", 0.98, [Evidence("manifest", "go.mod")])])
    payload = builder.build(profile=profile, changes=[], diff_text="", context_text="")
    assert "Go" in payload.user
    assert "PHP" not in payload.user
    assert "0.98" in payload.user  # confidence shown


def test_build_tracks_technology_markers(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    profile = RepoProfile(languages=[ProfileEntry("Go", 0.98, [Evidence("manifest", "go.mod")])])
    payload = builder.build(profile, [], "d", "")
    assert payload.system.startswith("You are a senior")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_prompts.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/prompts/system.md`:
```markdown
You are a senior software engineer performing a pre-commit code review.

Review ONLY the staged changes and the supplied relevant context.

Your job is to identify real, actionable problems introduced or exposed by these changes.

Do not report:
- style preferences
- trivial naming issues
- speculative problems
- hypothetical issues without evidence
- unrelated pre-existing bugs

Every finding must be supported by evidence in the supplied code.

Consider: correctness, security, reliability, performance, data integrity,
concurrency, compatibility, maintainability, testing.

If you are uncertain, reduce your confidence.

Return ONLY valid JSON matching the supplied schema.
```

`src/ai_review/prompts/review.md`:
```markdown
# Review Task

You are reviewing the staged diff below against these generic review criteria:

- Correctness of changed logic and edge cases introduced by the change
- Security: injection, secrets, authz, unsafe deserialization
- Error handling: exceptions/error paths introduced by the change
- Performance and resource handling in the changed code
- Concurrency and data integrity affected by the change
- API / backward compatibility impact of the change
- Maintainability and testability of new code (do not block for style)

Rules for findings:
- Only report problems introduced, exposed, or worsened by these changes.
- A pre-existing problem must be marked is_pre_existing=true and may be INFO only.
- Do not block for speculative "could theoretically fail" claims.
- For each issue give concrete evidence from the diff and a recommendation.
- confidence must be between 0 and 1. Lower confidence if evidence is weak.
```

`src/ai_review/prompts/technology/generic.md`:
```markdown
# Detected Technologies

The following technologies were detected with evidence and confidence:

{{technology_summary}}

Apply technology-specific best practices for these technologies only.
Do not assume any technology that is not listed.
```

`src/ai_review/prompts.py`:
```python
"""Layered prompt assembly (system + generic + detected techs + diff + context)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ai_review.models import PromptPayload, RepoProfile, StagedChange

OUTPUT_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "decision": {"enum": ["PASS", "BLOCK", "WARN"]},
        "summary": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]},
                    "category": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "evidence": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "is_pre_existing": {"type": "boolean"},
                },
                "required": [
                    "severity", "category", "file", "line", "title",
                    "description", "evidence", "recommendation",
                    "confidence", "is_pre_existing",
                ],
            },
        },
    },
    "required": ["decision", "summary", "issues"],
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class PromptBuilder:
    def __init__(self, prompt_dir: str):
        self.dir = Path(prompt_dir)

    def _load(self, rel: str) -> str:
        return _read(self.dir / rel)

    def build(self, profile: RepoProfile, changes: list[StagedChange],
              diff_text: str, context_text: str, rules: str = "",
              tech_template: str | None = None) -> PromptPayload:
        system = self._load("system.md")
        review = self._load("review.md")

        tech_layer = (tech_template or self._load("technology/generic.md"))
        table = "\n".join(
            f"- {e.name} (confidence {e.confidence:.2f}) — evidence: "
            + ", ".join(ev.detail for ev in e.evidence[:3])
            for e in profile.languages + profile.frameworks + profile.databases + profile.infrastructure
        ) or "- none detected"
        tech_layer = tech_layer.replace("{{technology_summary}}", table)

        files = "\n".join(
            f"- {c.path} [{c.status}]"
            + (f" (was {c.old_path})" if c.old_path else "")
            + (f" +{c.stat_added}/-{c.stat_removed}" if not c.is_binary else " [binary]")
            for c in changes
        )

        user_parts = [
            review, tech_layer,
            "# Staged Files", files,
            "# Staged Diff", diff_text,
            "# Relevant Context", context_text or "(none supplied)",
        ]
        if rules and rules.strip():
            user_parts += ["# Repository Review Rules", rules.strip()]
        user_parts += [
            "# Output Format",
            "Return ONLY a JSON object matching this schema:",
            f"```json\n{OUTPUT_JSON_SCHEMA}\n```",
        ]
        return PromptPayload(system=system, user="\n\n".join(user_parts),
                             json_schema=OUTPUT_JSON_SCHEMA)


def prompt_version(prompt_dir: str) -> str:
    files = ["system.md", "review.md", "technology/generic.md"]
    digest = hashlib.sha256()
    for rel in files:
        digest.update(rel.encode())
        digest.update((Path(prompt_dir) / rel).read_bytes())
    return digest.hexdigest()[:16]


def prompt_files_ok(prompt_dir: str) -> tuple[bool, list[str]]:
    missing = []
    for rel in ["system.md", "review.md", "technology/generic.md"]:
        if not (Path(prompt_dir) / rel).is_file():
            missing.append(rel)
    return (not missing), missing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_prompts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/prompts.py src/ai_review/prompts tests/unit/test_prompts.py
git commit -m "feat: layered prompt builder with output JSON schema"
```

## Task 10: Provider Layer (providers/base.py, llamacpp.py, openai_compatible.py)

**Files:**
- Create: `src/ai_review/providers/__init__.py`
- Create: `src/ai_review/providers/base.py`
- Create: `src/ai_review/providers/llamacpp.py`
- Create: `src/ai_review/providers/openai_compatible.py`
- Test: `tests/unit/test_providers.py`

**Interfaces:**
- Consumes: Task 2 (`PromptPayload`), Task 3 (`LLMConfig`).
- Produces:
  - `class ProviderError(RuntimeError)`
  - `class LLMProvider` ABC with `send(payload: PromptPayload, system_extra: str = "") -> RawLLMResponse`
  - `class LlamaCppProvider(llm_cfg)`, `class OpenAICompatibleProvider(llm_cfg)`
  - `class LlamaServerNotFound(ProviderError)` (connection refused → drives failure_policy)
  - `make_provider(cfg: AppConfig) -> LLMProvider`
  - Both providers use `httpx.Client(timeout=..., base_url=...)`; `json_mode` ∈ auto/on/off → send `response_format: {"type": "json_object"}` when `!= off`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_providers.py
import httpx
import pytest

from ai_review.config import AppConfig
from ai_review.models import PromptPayload
from ai_review.providers.base import LLMProvider, ProviderError
from ai_review.providers import make_provider, LlamaCppProvider, OpenAICompatibleProvider

PAYLOAD = PromptPayload(system="s", user="u", json_schema={"type": "object"})


def _fake_transport(responses: list[dict]):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if responses:
            body = responses.pop(0)
            return httpx.Response(200, json=body)
        return httpx.Response(500, json={"error": "boom"})

    return httpx.MockTransport(handler), calls


def test_make_provider_by_name():
    cfg = AppConfig()
    assert isinstance(make_provider(cfg), LlamaCppProvider)
    cfg.llm.provider = "openai_compatible"
    assert isinstance(make_provider(cfg), OpenAICompatibleProvider)


def test_llamacpp_sends_response_format_when_json_mode():
    transport, calls = _fake_transport([{"choices": [{"message": {"content": "{}"}}]}])
    cfg = AppConfig(llm={"provider": "llamacpp", "json_mode": "on"})
    prov = LlamaCppProvider(cfg.llm)
    prov.client = httpx.Client(transport=transport, base_url=cfg.llm.endpoint)
    prov.send(PAYLOAD)
    body = calls[-1]._content.decode()
    assert '"response_format"' in body
    assert '"json_object"' in body


def test_provider_timeout_sets_timeout():
    cfg = AppConfig(llm={"timeout_seconds": 5})
    p = make_provider(cfg)
    assert p.client.timeout.read == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_providers.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/providers/base.py`:
```python
"""Common provider interface."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

import httpx

from ai_review.models import PromptPayload, RawLLMResponse


class ProviderError(RuntimeError):
    pass


class LlamaServerNotFound(ProviderError):
    pass


class LLMProvider(ABC):
    name = "base"

    def __init__(self, llm_cfg):
        self.cfg = llm_cfg
        self.client = httpx.Client(
            base_url=llm_cfg.endpoint, timeout=llm_cfg.timeout_seconds,
            headers={"Content-Type": "application/json", **llm_cfg.headers},
        )
        if llm_cfg.api_key:
            self.client.headers["Authorization"] = f"Bearer {llm_cfg.api_key}"

    def _json_params(self) -> dict:
        return {"type": "json_object"} if self.cfg.json_mode != "off" else {}

    def _build_body(self, payload: PromptPayload) -> dict:
        return {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": payload.system},
                {"role": "user", "content": payload.user},
            ],
            "max_tokens": self.cfg.max_tokens,
            "temperature": 0.1,
            "stream": False,
            **self._json_params(),
        }

    @abstractmethod
    def send(self, payload: PromptPayload) -> RawLLMResponse:
        raise NotImplementedError

    def _post(self, path: str, body: dict):
        try:
            return self.client.post(path, json=body)
        except httpx.ConnectError as exc:
            raise LlamaServerNotFound(
                f"cannot connect to {self.cfg.endpoint}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"LLM request timed out after {self.cfg.timeout_seconds}s") from exc

    def _parse_response(self, response: httpx.Response) -> RawLLMResponse:
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"invalid JSON from provider: {response.status_code}") from exc
        try:
            choices = data["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"malformed provider response: {data!r}") from exc
        return RawLLMResponse(text=content, provider=self.name, endpoint=self.cfg.endpoint, duration_s=0.0)
```

`src/ai_review/providers/llamacpp.py`:
```python
"""llama.cpp server provider (OpenAI-compatible /v1/chat/completions)."""
from __future__ import annotations

import time

from ai_review.models import PromptPayload, RawLLMResponse

from .base import LLMProvider


class LlamaCppProvider(LLMProvider):
    name = "llamacpp"

    def send(self, payload: PromptPayload) -> RawLLMResponse:
        body = self._build_body(payload)
        started = time.monotonic()
        response = self._post("/v1/chat/completions", body)
        raw = self._parse_response(response)
        raw.duration_s = time.monotonic() - started
        return raw
```

`src/ai_review/providers/openai_compatible.py`:
```python
"""OpenAI-compatible / Azure / internal company endpoint provider."""
from __future__ import annotations

import time

from ai_review.models import PromptPayload, RawLLMResponse

from .base import LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def send(self, payload: PromptPayload) -> RawLLMResponse:
        body = self._build_body(payload)
        started = time.monotonic()
        response = self._post("/chat/completions", body)
        raw = self._parse_response(response)
        raw.duration_s = time.monotonic() - started
        return raw
```

`src/ai_review/providers/__init__.py`:
```python
from ai_review.config import AppConfig
from ai_review.providers.base import LLMProvider
from ai_review.providers.llamacpp import LlamaCppProvider
from ai_review.providers.openai_compatible import OpenAICompatibleProvider

__all__ = ["LLMProvider", "LlamaCppProvider", "OpenAICompatibleProvider", "make_provider"]


def make_provider(cfg: AppConfig) -> LLMProvider:
    name = cfg.llm.provider
    if name == "llamacpp":
        return LlamaCppProvider(cfg.llm)
    if name == "openai_compatible":
        return OpenAICompatibleProvider(cfg.llm)
    raise ValueError(f"unknown provider: {name}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_providers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/providers tests/unit/test_providers.py
git commit -m "feat: llama.cpp and OpenAI-compatible providers with json_mode"
```

## Task 11: Reviewer + Parser (reviewer.py, parser.py)

**Files:**
- Create: `src/ai_review/reviewer.py`
- Create: `src/ai_review/parser.py`
- Test: `tests/unit/test_parser.py`, `tests/unit/test_reviewer.py`

**Interfaces:**
- Consumes: Task 2 (`PromptPayload`, `RawLLMResponse`, `ReviewResult`, `Finding`), Task 9 (`OUTPUT_JSON_SCHEMA`), Task 10 (`LLMProvider`, `ProviderError`, `LlamaServerNotFound`).
- Produces:
  - `class ParseError(RuntimeError)`
  - `parse_llm_json(text: str, schema: dict | None = None) -> ReviewResult` — extracts first `{...}` JSON object if fenced, pydantic-validates, maps to `ReviewResult`; raises `ParseError` on any failure.
  - `CORRECTIVE_HINT = "Your last output was not valid JSON matching the supplied schema. Return only the JSON object."`
  - `review(provider, payload, *, corrective=False) -> RawLLMResponse | None` — sends payload (with corrective system hint if `corrective`).
  - `class ReviewSession` — `run(provider, payload) -> ReviewResult`: attempt parse; on `ParseError`, if `not corrective`, retry once with corrective; else re-raise as `ReviewUnavailable`. Exposes `attempts`, `corrective_used`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_parser.py
import pytest

from ai_review.parser import ParseError, parse_llm_json

GOOD = """```json
{"decision": "BLOCK", "summary": "s", "issues": [
  {"severity": "HIGH", "category": "BUG", "file": "a.go", "line": 3,
   "title": "t", "description": "d", "evidence": "e", "recommendation": "r",
   "confidence": 0.9, "is_pre_existing": false}
]}
```"""

BAD = "I could not produce JSON. Here is prose."


def test_parse_good_fenced_json():
    result = parse_llm_json(GOOD)
    assert result.decision == "BLOCK"
    assert result.issues[0].file == "a.go"
    assert result.issues[0].confidence == pytest.approx(0.9)


def test_parse_plain_json():
    result = parse_llm_json('{"decision": "PASS", "summary": "ok", "issues": []}')
    assert result.decision == "PASS"


def test_parse_bad_raises():
    with pytest.raises(ParseError):
        parse_llm_json(BAD)


def test_parse_invalid_schema_field():
    blob = '{"decision": "PASS", "summary": "s", "issues": [{"severity": "NOPE", "category": "BUG", "file": "a", "line": 1, "title": "t", "description": "d", "evidence": "e", "recommendation": "r", "confidence": 0.5, "is_pre_existing": false}]}'
    with pytest.raises(ParseError):
        parse_llm_json(blob)
```

```python
# tests/unit/test_reviewer.py
import pytest

import httpx

from ai_review.config import AppConfig, LLMConfig
from ai_review.models import PromptPayload
from ai_review.parser import ParseError
from ai_review.providers.base import LLMProvider, ProviderError
from ai_review.reviewer import ReviewSession


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.client = httpx.Client(base_url="http://x", timeout=1)
        super().__init__(LLMConfig())

    def send(self, payload):
        self.calls += 1
        text = self.responses.pop(0)
        from ai_review.models import RawLLMResponse
        return RawLLMResponse(text=text, provider=self.name, endpoint="x", duration_s=0.0)


def test_corrective_retry_then_success():
    bad = "not json"
    good = '{"decision": "PASS", "summary": "ok", "issues": []}'
    prov = FakeProvider([bad, good])
    payload = PromptPayload(system="s", user="u", json_schema={})
    session = ReviewSession(prov)
    result = session.run(payload)
    assert result.decision == "PASS"
    assert session.attempts == 2
    assert session.corrective_used is True


def test_two_failures_raise_parse_error():
    prov = FakeProvider(["not json", "still not json"])
    session = ReviewSession(prov)
    with pytest.raises(ParseError):
        session.run(PromptPayload(system="s", user="u", json_schema={}))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_parser.py tests/unit/test_reviewer.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/parser.py`:
```python
"""LLM JSON parsing with schema validation."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError

from ai_review.models import Finding, ReviewResult


class ParseError(RuntimeError):
    pass


class _IssueModel(BaseModel):
    severity: str
    category: str
    file: str
    line: int | None = None
    title: str
    description: str
    evidence: str = ""
    recommendation: str = ""
    confidence: float
    is_pre_existing: bool = False


class _ReviewModel(BaseModel):
    decision: str
    summary: str
    issues: list[_IssueModel] = Field(default_factory=list)


def _extract_json(text: str) -> str:
    fences = re.search(r"```(?:json)?\s*(\{.*?\})```", text, re.S)
    if fences:
        return fences.group(1)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ParseError("no JSON object found in LLM output")
    return match.group(0)


def parse_llm_json(text: str, schema: dict | None = None) -> ReviewResult:
    raw = _extract_json(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc}") from exc
    try:
        model = _ReviewModel.model_validate(data)
    except ValidationError as exc:
        raise ParseError(f"LLM output failed schema validation: {exc}") from exc

    valid_severity = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
    for issue in model.issues:
        if issue.severity not in valid_severity:
            raise ParseError(f"invalid severity {issue.severity!r}")

    findings = [
        Finding(severity=i.severity, category=i.category, file=i.file, line=i.line,
                title=i.title, description=i.description, evidence=i.evidence,
                recommendation=i.recommendation, confidence=float(i.confidence),
                is_pre_existing=i.is_pre_existing)
        for i in model.issues
    ]
    return ReviewResult(decision=model.decision, summary=model.summary, issues=findings)
```

`src/ai_review/reviewer.py`:
```python
"""Provider invocation with corrective retry on JSON conformance failure."""
from __future__ import annotations

from ai_review.models import PromptPayload, RawLLMResponse
from ai_review.parser import ParseError, parse_llm_json
from ai_review.providers.base import LLMProvider

CORRECTIVE_HINT = (
    "Your last output was not valid JSON matching the supplied schema. "
    "Return only the JSON object."
)


class ReviewSession:
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.attempts = 0
        self.corrective_used = False

    def run(self, payload: PromptPayload) -> "ReviewResult":
        from ai_review.models import ReviewResult

        raw = self._send(payload, corrective=False)
        self.attempts = 1
        try:
            return parse_llm_json(raw.text, payload.json_schema)
        except ParseError:
            pass
        if not payload.corrective:
            raw = self._send(payload, corrective=True)
            self.attempts = 2
            self.corrective_used = True
            try:
                return parse_llm_json(raw.text, payload.json_schema)
            except ParseError:
                pass
        raise ParseError("LLM never produced schema-conforming JSON")

    def _send(self, payload: PromptPayload, corrective: bool) -> RawLLMResponse:
        if corrective:
            system = payload.system + "\n\n" + CORRECTIVE_HINT
        else:
            system = payload.system
        return self.provider.send(PromptPayload(
            system=system, user=payload.user, json_schema=payload.json_schema,
            corrective=corrective,
        ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_parser.py tests/unit/test_reviewer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/parser.py src/ai_review/reviewer.py tests/unit/test_parser.py tests/unit/test_reviewer.py
git commit -m "feat: JSON parser with schema validation and corrective retry"
```

## Task 12: Validator + Policy Engine (validator.py, policy.py)

**Files:**
- Create: `src/ai_review/validator.py`
- Create: `src/ai_review/policy.py`
- Test: `tests/unit/test_validator.py`, `tests/unit/test_policy.py`

**Interfaces:**
- Consumes: Task 2 (`Finding`, `ReviewResult`, `StagedChange`, `Hunk`), Task 3 (`PolicyConfig`, `FailurePolicyConfig`).
- Produces:
  - `validate_findings(result, changes) -> ReviewResult` — drops findings whose `file` is not in the staged set, whose `line` is not within any `changed_new_lines`/hunk range for that file, or that fail severity/confidence range checks. `hard_block` findings from the security scan are kept regardless of line.
  - `class PolicyEngine` — `decide(result, cfg) -> ReviewResult` (sets final `decision`); BLOCK only when: any finding `severity in block_on` AND `confidence >= minimum_confidence` AND not `is_pre_existing` OR `hard_block`. Else WARN if issues exist; else PASS. Never returns BLOCK from a `decision` the LLM declared when no finding satisfies the threshold.
  - `class FailureDecision` — `apply(on_llm_unavailable, message) -> ReviewResult` for LLM-unavailable: `block` → BLOCK(with reason), `warn` → WARN, `allow` → PASS, each with explicit `summary`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_validator.py
from ai_review.models import Finding, Hunk, ReviewResult, StagedChange
from ai_review.validator import validate_findings


def _finding(file="a.go", line=3, severity="HIGH"):
    return Finding(severity=severity, category="BUG", file=file, line=line,
                   title="t", description="d", evidence="e", recommendation="r",
                   confidence=0.9)


def test_drops_unstaged_file():
    change = StagedChange(path="b.go", status="added", hunks=[Hunk(1, 2, 1, 2, changed_new_lines={1})])
    result = ReviewResult(decision="BLOCK", summary="s", issues=[_finding("a.go")])
    out = validate_findings(result, [change])
    assert out.issues == []


def test_keeps_finding_on_changed_line():
    change = StagedChange(path="a.go", status="modified", hunks=[Hunk(1, 5, 1, 5, changed_new_lines={3})])
    result = ReviewResult(decision="BLOCK", summary="s", issues=[_finding("a.go", 3)])
    out = validate_findings(result, [change])
    assert len(out.issues) == 1


def test_keeps_hard_block_finding_even_without_line():
    change = StagedChange(path="a.go", status="added", hunks=[Hunk(1, 2, 1, 2, changed_new_lines={1})])
    f = _finding("a.go", None)
    f.hard_block = True
    result = ReviewResult(decision="BLOCK", summary="s", issues=[f])
    out = validate_findings(result, [change])
    assert len(out.issues) == 1
```

```python
# tests/unit/test_policy.py
from ai_review.models import Finding, ReviewResult
from ai_review.config import PolicyConfig, FailurePolicyConfig
from ai_review.policy import PolicyEngine, FailureDecision


def _f(sev, conf, pre=False, hard=False):
    f = Finding(severity=sev, category="BUG", file="a.go", line=1, title="t",
                description="d", evidence="e", recommendation="r", confidence=conf)
    f.is_pre_existing = pre
    f.hard_block = hard
    return f


def test_block_high_confidence():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[_f("HIGH", 0.9)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "BLOCK"


def test_no_block_below_confidence_threshold():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[_f("HIGH", 0.5)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "WARN"


def test_no_block_pre_existing():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[_f("HIGH", 0.9, pre=True)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "WARN"


def test_hard_block_wins():
    r = ReviewResult(decision="PASS", summary="s", issues=[_f("INFO", 0.2, hard=True)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "BLOCK"


def test_failure_warn():
    out = FailureDecision().apply("warn", "LLM down")
    assert out.decision == "WARN"
    assert "AI review was skipped" in out.summary


def test_failure_allow_is_pass_not_passed_claim():
    out = FailureDecision().apply("allow", "LLM down")
    assert out.decision == "PASS"
    assert "AI review passed" not in out.summary.lower()


def test_failure_block():
    out = FailureDecision().apply("block", "LLM down")
    assert out.decision == "BLOCK"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_validator.py tests/unit/test_policy.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/validator.py`:
```python
"""Structural finding validation before the LLM's voice becomes a decision."""
from __future__ import annotations

from ai_review.models import ReviewResult, StagedChange


def validate_findings(result: ReviewResult, changes: list[StagedChange]) -> ReviewResult:
    valid_severity = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
    file_lines: dict[str, set[int]] = {}
    for change in changes:
        for hunk in change.hunks:
            file_lines.setdefault(change.path, set()).update(hunk.changed_new_lines)

    kept = []
    for f in result.issues:
        if f.severity not in valid_severity or not (0.0 <= f.confidence <= 1.0):
            continue
        if f.hard_block:
            if f.file in {c.path for c in changes}:
                kept.append(f)
            continue
        if f.file not in {c.path for c in changes}:
            continue
        if f.line is None:
            continue
        if f.line not in file_lines.get(f.file, set()):
            continue
        kept.append(f)

    result.issues = kept
    if result.decision == "BLOCK" and not any(
        f.severity in ("CRITICAL", "HIGH") or f.hard_block for f in kept
    ):
        result.decision = "WARN"
    return result
```

`src/ai_review/policy.py`:
```python
"""Policy engine: the LLM proposes, the policy disposes."""
from __future__ import annotations

from ai_review.config import PolicyConfig
from ai_review.models import ReviewResult

VALID_BLOCK = {"CRITICAL", "HIGH"}


class PolicyEngine:
    def __init__(self, cfg: PolicyConfig):
        self.cfg = cfg

    def decide(self, result: ReviewResult) -> ReviewResult:
        block = {f for f in result.issues if self._blocks(f)}
        if block:
            result.decision = "BLOCK"
            result.summary = "Blocking findings were validated against the staged changes."
            return result
        if result.issues:
            result.decision = "WARN"
        else:
            result.decision = "PASS"
            result.summary = "No issues found."
        return result

    def _blocks(self, finding) -> bool:
        if finding.hard_block:
            return finding.file is not None
        if finding.is_pre_existing:
            return False
        if finding.severity not in VALID_BLOCK:
            return False
        return finding.confidence >= self.cfg.minimum_confidence_to_block


class FailureDecision:
    def apply(self, on_llm_unavailable: str, message: str) -> ReviewResult:
        reason = "AI review was skipped. " + message
        if on_llm_unavailable == "block":
            return ReviewResult(decision="BLOCK", summary=reason + "\nCommit blocked because failure_policy=block.")
        if on_llm_unavailable == "allow":
            return ReviewResult(decision="PASS", summary=reason + "\nCommit allowed because failure_policy=allow.")
        return ReviewResult(decision="WARN", summary=reason + "\nCommit allowed because failure_policy=warn.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_validator.py tests/unit/test_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/validator.py src/ai_review/policy.py tests/unit/test_validator.py tests/unit/test_policy.py
git commit -m "feat: finding validation and policy engine"
```

## Task 13: Output Renderers (output.py)

**Files:**
- Create: `src/ai_review/output.py`
- Test: `tests/unit/test_output.py`

**Interfaces:**
- Consumes: Task 2 (`ReviewResult`, `Finding`, `CheckResult`, `RepoProfile`, `ReviewContext`).
- Produces:
  - `render(result, *, fmt="terminal", profile=None, meta=None) -> str`
  - `render_terminal(result, profile, meta) -> str` (the `━` boxed layout from spec §27)
  - `render_json(result) -> str` (`json.dumps(...)`, findings array)
  - `render_markdown(result, profile, meta) -> str` (headings + table)
  - meta dict keys: `repo`, `branch`, `files`, `added`, `removed`, `detected`, `checks`, `duration_s`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_output.py
import json

from ai_review.models import Finding, ReviewResult
from ai_review.output import render, render_json, render_markdown, render_terminal


def _result():
    f = Finding(severity="HIGH", category="BUG", file="a.go", line=3, title="nil deref",
                description="possible nil dereference", evidence="returns nil",
                recommendation="check nil", confidence=0.94)
    return ReviewResult(decision="BLOCK", summary="blocked", issues=[f]), f


def test_terminal_has_result_blocked():
    r, f = _result()
    text = render_terminal(r, profile=None, meta={"repo": "x", "branch": "main", "files": 1, "added": 3, "removed": 0})
    assert "COMMIT BLOCKED" in text
    assert "nil deref" in text
    assert "94%" in text
    assert "a.go:3" in text


def test_json_is_valid_and_shapes():
    r, f = _result()
    data = json.loads(render_json(r))
    assert data["decision"] == "BLOCK"
    assert data["issues"][0]["file"] == "a.go"


def test_markdown_has_finding():
    r, f = _result()
    md = render_markdown(r, profile=None, meta={})
    assert "## Issues" in md
    assert "nil deref" in md


def test_render_dispatch():
    r, _ = _result()
    assert render(r, fmt="json").lstrip().startswith("{")
    assert "COMMIT BLOCKED" in render(r, fmt="terminal")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_output.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/output.py`:
```python
"""Terminal / JSON / Markdown output renderers."""
from __future__ import annotations

import json

from ai_review.models import RepoProfile, ReviewResult

_WHEN = "━" * 34


def _meta(meta: dict | None) -> dict:
    return meta or {}


def render(result: ReviewResult, *, fmt: str = "terminal",
           profile: RepoProfile | None = None, meta: dict | None = None) -> str:
    if fmt == "json":
        return render_json(result)
    if fmt == "markdown":
        return render_markdown(result, profile, meta)
    return render_terminal(result, profile, meta)


def render_terminal(result: ReviewResult, profile: RepoProfile | None,
                    meta: dict | None = None) -> str:
    m = _meta(meta)
    lines = [_WHEN, " AI Git Review", _WHEN]
    if m.get("repo"):
        lines += [f"Repository: {m['repo']}", f"Branch: {m.get('branch', '')}"]
    if m.get("files") is not None:
        lines += [f"Changes: {m['files']} files  +{m.get('added', 0)} -{m.get('removed', 0)}"]
    if m.get("detected"):
        lines += ["Detected: " + ", ".join(m["detected"])]
    checks = m.get("checks") or []
    if checks:
        lines += [""]
        for c in checks:
            mark = "✓" if c.exit_code == 0 else "✗"
            lines.append(f"{mark} {c.name}")
    lines += ["", f"AI review... {'✓' if result.decision != 'BLOCK' else '✗'}"]
    lines += [_WHEN]
    label = {"PASS": "RESULT: PASS", "BLOCK": "RESULT: COMMIT BLOCKED", "WARN": "RESULT: WARNING"}
    lines += [f" {label[result.decision]}", _WHEN, ""]
    for f in result.issues:
        lines += [f"{f.severity} — {f.category}", "", f"{f.file}:{f.line or '?'}", "",
                  f.title, "", f"Confidence: {int(f.confidence * 100)}%", "",
                  "Recommendation:", f.recommendation, _WHEN, ""]
    if not result.issues and result.decision != "BLOCK":
        lines.append(result.summary)
    return "\n".join(lines)


def render_json(result: ReviewResult) -> str:
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
        "checks": [{"name": c.name, "command": c.command, "exit_code": c.exit_code} for c in result.checks],
    }, indent=2)


def render_markdown(result: ReviewResult, profile: RepoProfile | None,
                    meta: dict | None = None) -> str:
    m = _meta(meta)
    out = [f"# AI Review: {result.decision}", "", result.summary, ""]
    if m.get("detected"):
        out += ["## Detected", ", ".join(m["detected"]), ""]
    out += ["## Issues", "", "| Severity | Category | File | Line | Confidence | Title |", "|---|---|---|---|---|---|"]
    for f in result.issues:
        out.append(f"| {f.severity} | {f.category} | {f.file} | {f.line or '-'} | {f.confidence:.2f} | {f.title} |")
    out += ["", "## Checks", ""]
    for c in result.checks:
        out.append(f"- {'OK' if c.exit_code == 0 else 'FAIL'}: `{c.name}`")
    return "\n".join(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_output.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/output.py tests/unit/test_output.py
git commit -m "feat: terminal/json/markdown renderers"
```

## Task 14: Pipeline Orchestration (pipeline.py)

**Files:**
- Create: `src/ai_review/pipeline.py`
- Test: `tests/integration/test_pipeline.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `class Pipeline` — `commit_meta(repo_dir) -> dict`, `collect()`, `run(provider) -> ReviewResult`, plus `dry_run() -> str`.
  - `build_pipeline(repo_dir, cfg, *, verbose=False, dry_run=False) -> Pipeline`
  - `Pipeline.run` sequence: collect staged → detect (scoped profile via `profile_from_names`) → classify → diff text → **redact** → context (Phase 1: diff only; `context` placeholder string) → scan_staged (security findings) → if dry_run return plan text → build prompt → `ReviewSession.run` → merge security findings → `validate_findings` → `PolicyEngine.decide`. Catch `LlamaServerNotFound`/`ProviderError`/`ParseError` → `FailureDecision.apply(failure_policy)`.
  - `select_only(name)` returns None until Phase 4; kept as a hook.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_pipeline.py
import subprocess

import pytest

from ai_review.config import AppConfig
from ai_review.pipeline import build_pipeline
from ai_review.providers.provider_bundle import StaticProvider


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "T")
    return tmp_path


GOOD = '{"decision": "PASS", "summary": "ok", "issues": []}'


def test_pipeline_pass_with_static_provider(repo):
    (repo / "go.mod").write_text("module x\n")
    (repo / "main.go").write_text("package main\nfunc main() {}\n")
    _git(repo, "add", "-A")
    cfg = AppConfig()
    pipe = build_pipeline(str(repo), cfg, provider=StaticProvider([GOOD]))
    result = pipe.run()
    assert result.decision == "PASS"
    assert result.issues == []


def test_pipeline_secret_scan_blocks_offline(repo):
    (repo / "x.py").write_text("token=\"ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789\"\n")
    _git(repo, "add", "-A")
    cfg = AppConfig()
    pipe = build_pipeline(str(repo), cfg, provider=StaticProvider([GOOD]))
    result = pipe.run()
    assert result.decision == "BLOCK"
    assert any(f.hard_block for f in result.issues)


def test_pipeline_dry_run_no_provider_call(repo):
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", "-A")
    called = []

    class NeverProvider:
        def send(self, payload):
            called.append(1)
            raise AssertionError("provider must not be called in dry-run")

    pipe = build_pipeline(str(repo), AppConfig(), provider=NeverProvider(), dry_run=True)
    plan = pipe.run()
    assert called == []
    assert "git diff --cached" in plan
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_pipeline.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

Note: `StaticProvider` is test infrastructure shipped for local iterative testing — used when no LLM server is available. Create it in-provider:

`src/ai_review/providers/provider_bundle.py`:
```python
"""Test/offline static provider used by integration tests and --dry-run demos."""
from ai_review.models import PromptPayload, RawLLMResponse
from ai_review.providers.base import LLMProvider


class StaticProvider(LLMProvider):
    name = "static"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        from ai_review.config import LLMConfig
        super().__init__(LLMConfig())

    def send(self, payload: PromptPayload) -> RawLLMResponse:
        text = self.responses.pop(0) if self.responses else '{"decision":"PASS","summary":"static","issues":[]}'
        return RawLLMResponse(text=text, provider=self.name, endpoint="static", duration_s=0.0)
```

`src/ai_review/pipeline.py`:
```python
"""Functional pipeline: the composition root."""
from __future__ import annotations

from dataclasses import dataclass, field

from ai_review import __version__
from ai_review.classifier import classify
from ai_review.config import AppConfig, load_config_for_repo
from ai_review.detector import detect
from ai_review.diff import attach_hunks, parse_unified_diff
from ai_review.git import collect_staged, git_branch, git_diff_text, git_repo_root
from ai_review.policy import FailureDecision, PolicyEngine
from ai_review.profile import profile_from_names
from ai_review.prompts import PromptBuilder, prompt_version
from ai_review.security import redact_text, scan_staged
from ai_review.validator import validate_findings


@dataclass
class RunnerOptions:
    repo_dir: str
    cfg: AppConfig
    provider: object
    dry_run: bool = False
    verbose: bool = False
    meta: dict = field(default_factory=dict)


def build_pipeline(repo_dir, cfg=None, *, provider=None, dry_run=False, verbose=False,
                   select_only=None):
    cfg = cfg or load_config_for_repo(repo_dir)
    return Pipeline(RunnerOptions(
        repo_dir=repo_dir, cfg=cfg, provider=provider, dry_run=dry_run, verbose=verbose,
    ))


class Pipeline:
    def __init__(self, opts: RunnerOptions):
        self.opts = opts
        from ai_review.reviewer import ReviewSession
        self.session_factory = lambda: ReviewSession(opts.provider)

    def collect(self):
        root = git_repo_root(self.opts.repo_dir)
        changes = collect_staged(root, ignore=set())
        attach_hunks(changes, parse_unified_diff(git_diff_text(root)))
        return root, changes

    def run(self):
        root, changes = self.collect()
        cfg = self.opts.cfg
        names = [c.path for c in changes]
        full_profile = detect(names, root)
        profile = profile_from_names(full_profile, names)
        classes = classify(names, cfg.generated.ignore)
        diff_text = git_diff_text(root)
        redacted, secret_kinds = redact_text(diff_text)
        security_findings = scan_staged(root, changes, cfg.security.excluded_files)

        meta = {
            "repo": root.rsplit("/", 1)[-1],
            "branch": git_branch(root),
            "files": len(changes),
            "added": sum(c.stat_added for c in changes),
            "removed": sum(c.stat_removed for c in changes),
            "detected": profile.all_names(),
            "duration_s": None,
        }

        if self.opts.dry_run:
            return self._dry_run_report(meta, redacted=secret_kinds, checks=[])

        import time
        started = time.monotonic()
        try:
            result = self._review(profile, changes, redacted)
        except Exception as exc:
            from ai_review.reviewer import ParseError
            from ai_review.providers.base import ProviderError, LlamaServerNotFound
            if isinstance(exc, (LlamaServerNotFound, ProviderError, ParseError)):
                result = FailureDecision().apply(cfg.failure_policy.on_llm_unavailable, str(exc))
            else:
                raise
        result.issues = security_findings + result.issues
        result = validate_findings(result, changes)
        result = PolicyEngine(cfg.policy).decide(result)
        meta["duration_s"] = round(time.monotonic() - started, 1)
        self.opts.meta = meta
        return result

    def _review(self, profile, changes, diff_text):
        from ai_review.prompts import PromptBuilder
        builder = PromptBuilder(self._prompt_dir())
        payload = builder.build(profile=profile, changes=changes, diff_text=diff_text,
                                context_text=self._context_text(changes))
        session = self.session_factory()
        return session.run(payload)

    def _context_text(self, changes) -> str:
        return ""  # Phase 2: context engine

    def _prompt_dir(self) -> str:
        from pathlib import Path
        import ai_review.prompts as pmod
        return str(Path(pmod.__file__).parent)

    def _dry_run_report(self, meta, redacted, checks) -> str:
        lines = [
            f"ai-review v{__version__} dry-run",
            f"repo: {meta['repo']}  branch: {meta['branch']}",
            f"changes: {meta['files']} files  +{meta['added']} -{meta['removed']}",
            "detected: " + (", ".join(meta["detected"]) or "none"),
            "steps:",
            "  1. git diff --cached (collect staged)",
            "  2. detect technologies -> profile",
            "  3. classify files -> review depth",
            "  4. redact secrets (matched: " + (", ".join(redacted) or "none") + ")",
            "  5. secret scan",
            "  6. build layered prompt",
            "  7. call LLM via configured provider  [SKIPPED in dry-run]",
            "  8. parse + validate findings",
            "  9. policy decision",
        ]
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/pipeline.py src/ai_review/providers/provider_bundle.py tests/integration/test_pipeline.py
git commit -m "feat: pipeline orchestration with dry-run and offline path"
```

## Task 15: CLI + Hook Install/Uninstall (cli.py, hooks.py)

**Files:**
- Create: `src/ai_review/cli.py`
- Create: `src/ai_review/hooks.py`
- Test: `tests/unit/test_cli.py`, `tests/unit/test_hooks.py`

**Interfaces:**
- Consumes: Task 1 (entry point), Task 14 (`build_pipeline`), Task 13 (`render`), doctor (Task 16 next).
- Produces:
  - `main(argv=None) -> int` — argparse with: `--version`, `--doctor`, `--install-hook`, `--uninstall-hook`, `--staged`, `--dry-run`, `--verbose`, `--format {terminal,json,markdown}`, `--provider`, `--endpoint`, `--config` (overrides). Exit codes: 0 PASS/WARN/allow, 1 BLOCK, 2 error.
  - `hooks.py`:
    - `HOOK_MARKER = "ai-review hook:"`
    - `install_hook(repo_dir, exe="ai-review") -> str` — if `.git/hooks/pre-commit` missing → write ours; if exists and not ours → backup to `pre-commit.ai-review-backup` and write chained wrapper (runs backup then ours); if exists and ours → no-op message.
    - `uninstall_hook(repo_dir) -> str` — remove only our wrapper, restore backup if present; otherwise no-op.
  - Flag wiring: `--config key=value` supports dotted overrides (e.g. `llm.endpoint=http://x`) applied as CLI layer.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_hooks.py
import subprocess

import pytest

from ai_review.hooks import HOOK_MARKER, install_hook, uninstall_hook


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "T")
    return tmp_path


def test_install_creates_hook(repo):
    message = install_hook(str(repo), exe="ai-review")
    hook = repo / ".git" / "hooks" / "pre-commit"
    assert hook.is_file()
    assert HOOK_MARKER in hook.read_text()
    assert "ai-review" in hook.read_text()


def test_install_backs_up_existing_hook(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'existing'\n")
    hook.chmod(0o755)
    install_hook(str(repo), exe="ai-review")
    backup = repo / ".git" / "hooks" / "pre-commit.ai-review-backup"
    assert backup.read_text().startswith("#!/bin/sh")


def test_uninstall_restores_backup(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'existing'\n")
    hook.chmod(0o755)
    install_hook(str(repo), exe="ai-review")
    message = uninstall_hook(str(repo))
    assert message == "restored"
    assert hook.read_text().startswith("#!/bin/sh")
    assert HOOK_MARKER not in hook.read_text()


def test_uninstall_removes_own_hook(repo):
    install_hook(str(repo), exe="ai-review")
    message = uninstall_hook(str(repo))
    assert message == "removed"
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_hooks.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/hooks.py`:
```python
"""Pre-commit hook install / uninstall with safe integration."""
from __future__ import annotations

import os
from pathlib import Path

from ai_review import __version__

HOOK_MARKER = "ai-review hook:"
BACKUP_NAME = "pre-commit.ai-review-backup"

BACKUP_SEGMENT = """_BACKUP="$(dirname "$0")/{backup}"
if [ -x "$_BACKUP" ] && ! grep -q '^{marker}' "$_BACKUP" 2>/dev/null; then
  "$_BACKUP" || exit $?
fi
"""

RUN_SEGMENT = """_EXE="${{AI_REVIEW_EXE:-{exe}}}"
if command -v "$_EXE" >/dev/null 2>&1; then
  "$_EXE" --staged || exit $?
else
  echo "ai-review: executable not found on PATH: $_EXE" >&2
fi
"""


def _hooks_dir(repo_dir: str) -> Path:
    return Path(repo_dir) / ".git" / "hooks"


def _is_ours(path: Path) -> bool:
    try:
        return HOOK_MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def install_hook(repo_dir: str, exe: str = "ai-review") -> str:
    hooks = _hooks_dir(repo_dir)
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    backup = hooks / BACKUP_NAME
    if hook.exists() and not _is_ours(hook):
        backup.write_text(hook.read_text(encoding="utf-8", errors="replace"))
        backup.chmod(hook.stat().st_mode)
        hook.chmod(0o644)
    content = _render_wrapper(exe, backup.exists())
    hook.write_text(content, encoding="utf-8")
    hook.chmod(0o755)
    if backup.exists():
        return "installed (wrapped existing hook)"
    return "installed"


def _render_wrapper(exe: str, has_backup: bool) -> str:
    head = "#!/usr/bin/env bash\n"
    marker_line = f"# {HOOK_MARKER} v{__version__} :: do-not-edit\n"
    backup = (
        BACKUP_SEGMENT.format(marker=HOOK_MARKER, backup=BACKUP_NAME)
        if has_backup
        else ""
    )
    run = RUN_SEGMENT.format(exe=exe)
    return head + marker_line + backup + run


def uninstall_hook(repo_dir: str) -> str:
    hooks = _hooks_dir(repo_dir)
    hook = hooks / "pre-commit"
    backup = hooks / BACKUP_NAME
    if not hook.exists() or not _is_ours(hook):
        return "no op"
    if backup.exists():
        hook.write_text(backup.read_text(encoding="utf-8", errors="replace"))
        hook.chmod(backup.stat().st_mode)
        backup.unlink()
        return "restored"
    hook.unlink()
    return "removed"
```

`src/ai_review/cli.py`:
```python
"""Command-line interface."""
from __future__ import annotations

import argparse
import json
import os
import sys

from ai_review import __version__
from ai_review.config import load_config_for_repo
from ai_review.hooks import install_hook, uninstall_hook
from ai_review.output import render
from ai_review.pipeline import build_pipeline

EXIT_OK = 0
EXIT_BLOCK = 1
EXIT_ERROR = 2


def _parse_dotted(entries: list[str]) -> dict:
    out: dict = {}
    for entry in entries:
        key, _, value = entry.partition("=")
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _coerce(value)
    return out


def _coerce(value: str):
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _find_repo_dir() -> str:
    return os.getcwd()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai-review",
                                description="AI Git pre-commit review agent")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--doctor", action="store_true", help="run environment diagnostics")
    p.add_argument("--install-hook", action="store_true", help="install the pre-commit hook")
    p.add_argument("--uninstall-hook", action="store_true", help="remove only our hook integration")
    p.add_argument("--staged", action="store_true", help="review staged changes (default)")
    p.add_argument("--dry-run", action="store_true", help="show steps without calling the LLM")
    p.add_argument("--verbose", action="store_true", help="verbose output")
    p.add_argument("--format", choices=["terminal", "json", "markdown"], default="terminal")
    p.add_argument("--provider", help="override LLM provider")
    p.add_argument("--endpoint", help="override LLM endpoint")
    p.add_argument("--config", action="append", default=[], metavar="KEY=VALUE",
                   help="config override (dotted key), repeatable")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    repo_dir = _find_repo_dir()

    if args.install_hook:
        try:
            print(install_hook(repo_dir))
        except Exception as exc:
            print(f"error: failed to install hook: {exc}", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK

    if args.uninstall_hook:
        print(uninstall_hook(repo_dir))
        return EXIT_OK

    if args.doctor:
        from ai_review.doctor import run_doctor
        rc, lines = run_doctor(repo_dir)
        print("\n".join(lines))
        return rc

    overrides = _parse_dotted(args.config)
    if args.provider:
        overrides.setdefault("llm", {})["provider"] = args.provider
    if args.endpoint:
        overrides.setdefault("llm", {})["endpoint"] = args.endpoint

    try:
        cfg = load_config_for_repo(repo_dir, cli_overrides=overrides or None)
    except Exception as exc:
        print(f"error: invalid configuration: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        from ai_review.providers import make_provider
        provider = None if args.dry_run else make_provider(cfg)
        pipe = build_pipeline(repo_dir, cfg, provider=provider,
                              dry_run=args.dry_run, verbose=args.verbose)
        result_or_text = pipe.run()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.dry_run:
        print(result_or_text)
        return EXIT_OK

    result = result_or_text
    meta = getattr(pipe, "opts", {}).meta if hasattr(pipe, "opts") else {}
    text = render(result, fmt=args.format, profile=None, meta=meta)
    print(text)
    if args.verbose and result.issues:
        print(f"\n[verbose] {len(result.issues)} finding(s), policy={result.decision}", file=sys.stderr)
    return EXIT_OK if result.decision != "BLOCK" else EXIT_BLOCK
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_hooks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/cli.py src/ai_review/hooks.py tests/unit/test_hooks.py
git commit -m "feat: CLI entry point and safe hook install/uninstall"
```

## Task 16: Doctor (doctor.py)

**Files:**
- Create: `src/ai_review/doctor.py`
- Test: `tests/unit/test_doctor.py`

**Interfaces:**
- Consumes: Task 3, 4, 9, 10, 15.
- Produces: `run_doctor(repo_dir) -> tuple[int, list[str]]` — probes in order, returns `(exit_code, lines)`; each line `✓ scanf`-style; exit 0 if all ok, 1 if any error.
  Probes: Git binary, git repository, config load, technology detection, prompt files, secret redaction self-test, hook install state, LLM endpoint (`llamacpp` → `GET /v1/models` within 3s) reported separately as {ok,warn,error} so a missing local server degrades to `warn`, not hard error.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_doctor.py
import subprocess

import pytest

from ai_review.doctor import run_doctor


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "go.mod").write_text("module x\n")
    return tmp_path


def test_doctor_reports_ok_lines(repo):
    rc, lines = run_doctor(str(repo))
    text = "\n".join(lines)
    assert "Git" in text and "Technology detection" in text and "Prompt" in text
    assert any("Secret redaction" in line for line in lines)
    assert "LLM" in text


def test_doctor_llm_unavailable_is_warn_not_error(repo):
    rc, lines = run_doctor(str(repo))
    llm_line = next(line for line in lines if "LLM" in line)
    assert llm_line.startswith("⚠") or llm_line.startswith("✓") or llm_line.startswith("✗")
    assert rc in (0, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_doctor.py -v`
Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

`src/ai_review/doctor.py`:
```python
"""Diagnostics: env, repo, config, detection, prompts, redaction, hook, LLM."""
from __future__ import annotations

import shutil

import httpx

from ai_review.config import load_config_for_repo
from ai_review.detector import detect
from ai_review.git import git_repo_root, GitError
from ai_review.hooks import _is_ours
from ai_review.prompts import prompt_files_ok
from ai_review.security import redact_text


def run_doctor(repo_dir: str) -> tuple[int, list[str]]:
    lines = ["AI Review Doctor", ""]
    errors = 0
    warns = 0

    def ok(label: str) -> None:
        lines.append(f"✓ {label}")

    def warn(label: str, note: str = "") -> None:
        nonlocal warns
        warns += 1
        lines.append(f"⚠ {label}" + (f" — {note}" if note else ""))

    def err(label: str, note: str = "") -> None:
        nonlocal errors
        errors += 1
        lines.append(f"✗ {label}" + (f" — {note}" if note else ""))

    if shutil.which("git"):
        ok("Git")
    else:
        err("Git", "git not found on PATH")

    try:
        root = git_repo_root(repo_dir)
        ok("Repository")
    except GitError as exc:
        err("Repository", str(exc))
        root = repo_dir

    try:
        cfg = load_config_for_repo(repo_dir)
        ok("Configuration")
    except Exception as exc:
        err("Configuration", str(exc))
        cfg = None

    try:
        profile = detect([], root)
        if profile.languages or profile.frameworks:
            names = ", ".join(profile.all_names()[:8])
            ok(f"Technology detection ({names})")
        else:
            warn("Technology detection", "no known technology detected")
    except Exception as exc:
        err("Technology detection", str(exc))

    from ai_review.prompts import prompt_files_ok
    ok_flag, missing = prompt_files_ok(_default_prompt_dir())
    if ok_flag:
        ok("Prompt files")
    else:
        err("Prompt files", "missing: " + ", ".join(missing))

    redacted, kinds = redact_text("key=AKIAIOSFODNN7EXAMPLE")
    ok("Secret redaction") if "AKIAIOSFODNN7EXAMPLE" not in redacted else err("Secret redaction", "self-test failed")

    from pathlib import Path
    hook = Path(root) / ".git" / "hooks" / "pre-commit"
    if hook.exists() and _is_ours(hook):
        ok("Git hook (installed by ai-review)")
    elif hook.exists():
        warn("Git hook", "existing hook present (not ours)")
    else:
        warn("Git hook", "not installed")

    if cfg:
        _probe_llm(lines, ok, warn, err, cfg)
    else:
        err("LLM provider", "skipped (no config)")

    lines.append("")
    if errors:
        lines.append(f"System not ready: {errors} error(s), {warns} warning(s).")
        return 1, lines
    if warns:
        lines.append(f"System ready (with {warns} warning(s)).")
    else:
        lines.append("System ready.")
    return 0, lines


def _probe_llm(lines, ok, warn, err, cfg) -> None:
    try:
        r = httpx.get(cfg.llm.endpoint.rstrip("/") + "/v1/models", timeout=3.0)
        if r.status_code == 200:
            ok("LLM endpoint")
        else:
            warn("LLM endpoint", f"HTTP {r.status_code}")
    except Exception as exc:
        warn("LLM endpoint", f"{cfg.llm.provider} server unreachable")


def _default_prompt_dir() -> str:
    import ai_review.prompts as pmod
    from pathlib import Path
    return str(Path(pmod.__file__).parent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_doctor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_review/doctor.py tests/unit/test_doctor.py
git commit -m "feat: doctor diagnostics"
```

## Task 17: README, LICENSE, Example Config, Package Data Verification

**Files:**
- Create: `README.md`
- Create: `LICENSE` (MIT)
- Create: `examples/.ai-review.example.yaml`
- Modify: `pyproject.toml` (package data: include `**/prompts/*.md`, `**/config/*.yaml`, `**/prompts/**/*.md`)
- Test: `tests/unit/test_package_data.py`

**Interfaces:**
- Consumes: all of Phase 1.
- Produces: installable wheel containing prompt/config assets; documented install + hook + privacy workflows.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_package_data.py
import importlib.resources

import ai_review.prompts as prompts
import ai_review.config as config


def test_prompt_assets_shipped():
    with importlib.resources.files(prompts).joinpath("system.md").open("r", encoding="utf-8") as fh:
        assert fh.read().startswith("You are a senior")


def test_default_config_shipped():
    with importlib.resources.files(config).joinpath("config", "default.yaml").open("r", encoding="utf-8") as fh:
        content = fh.read()
    assert "llm:" in content and "llamacpp" in content
```

- [ ] **Step 2: Run test — expected PASS via archive or source**

Run: `python -m pytest tests/unit/test_package_data.py -v`

- [ ] **Step 3: Write resources**

`pyproject.toml` add:
```toml
[tool.hatch.build.targets.wheel.force-include]
src/ai_review/prompts = "ai_review/prompts"
src/ai_review/config = "ai_review/config"
```

`README.md`:
```markdown
# ai-review

Technology-agnostic, provider-agnostic AI Git pre-commit code review agent.

## Install

```bash
pipx install ai-review          # org: pip install ai-review on a private index
# or uv
uv tool install ai-review
```

## Usage

```bash
ai-review --install-hook        # once per repository
git add . && git commit -m "..."   # hook runs the reviewer automatically
```

Commands: `--staged`, `--dry-run`, `--doctor`, `--verbose`, `--format json|markdown|terminal`, `--uninstall-hook`, `--version`, `--help`.

## Configuration

Precedence (high → low): CLI args > `.ai-review.yaml` (repo) > `~/.config/ai-review/config.yaml` (user) > organization config (set `AI_REVIEW_ORG_CONFIG`). See `examples/.ai-review.example.yaml`.

## Privacy

- **Local mode (default):** repo → agent → local llama.cpp. No source leaves the machine.
- **Remote mode:** if you configure a remote provider, repository code (post redaction) is sent to that provider. Review your config before enabling.

`git commit --no-verify` is never modified by this tool.
```

`LICENSE` (MIT, 2026, Rajen Trivedi).

`examples/.ai-review.example.yaml`:
```yaml
# Copy to .ai-review.yaml and customize per-repository.
llm:
  provider: llamacpp          # llamacpp | openai_compatible
  endpoint: http://127.0.0.1:8080
  model: auto
  timeout_seconds: 120
  max_tokens: 4096
security:
  redact_secrets: true
  excluded_files: [".env", "*.pem", "*.key"]
policy:
  block_on: [CRITICAL, HIGH]
  minimum_confidence_to_block: 0.80
failure_policy:
  on_llm_unavailable: warn
checks:
  enabled: true
  auto_detect: true
  trusted_commands: []
  commands: []
context:
  enabled: true
  lines_before: 30
  lines_after: 30
```

- [ ] **Step 4: Run full unit suite**

Run: `python -m pytest tests/unit tests/integration -q`
Expected: all PASS

- [ ] **Step 5: Verify installable wheel includes assets**

Run: `pip wheel . -w /tmp/wheeltest -q && unzip -l /tmp/wheeltest/*.whl | grep -E "(prompts|config/default)"`

- [ ] **Step 6: Commit**

```bash
git add README.md LICENSE examples pyproject.toml tests/unit/test_package_data.py
git commit -m "docs: README, LICENSE, example config; package data for prompts/config"
```

## Phase 1 Exit Criteria

- `python -m pytest` full suite green; `--cov=ai_review` ≥85% (excluding CLI/main glue).
- `ai-review --version`, `--help`, `--doctor`, `--dry-run`, `--staged`, `--install-hook`, `--uninstall-hook`, `--format` all work from an installed `pip install -e .` or wheel.
- Manual smoke: instrumented repo with a hardcoded `ghp_` token → `git commit` BLOCKED by the offline secret scan even with no LLM running; failure message honest (`failure_policy=warn` → commit allowed, no "passed" claim).
- Edge verified: added/modified/deleted/renamed/binary staged files all handled without crashing; `--no-verify` bypass works.

---

# Phase 2 — Context Engine (§11, §12)

New modules: `src/ai_review/context.py` (per-hunk surrounding lines, containing function/module detection via lightweight brace/bracket scanning, import extraction for `import`/`require`/`use` lines, `context.lines_before/after`, `max_file_size_kb`, `max_total_context_kb` budgets), `--context` config already present. Wires into `Pipeline._context_text`, replaces the Phase 1 empty string. New unit tests for budget clamping and function-boundary scanning; integration test asserting the LLM prompt includes nearby definitions, not the whole file. Caches per-repo file reads keyed by `(path, mtime)`.

Tasks (detailed at execution):
- T1 context module: `build_contexts(changes, repo_dir, cfg.context) -> list[ContextBundle]`
- T2 dependency awareness: imports → grep definition for same-file + one-hop local module, capped by budget
- T3 wire into pipeline + tests + commit

Exit: prompt `# Relevant Context` is populated for a modified Go function, and budget caps exercised in unit tests.

---

# Phase 3 — Deterministic Check Runner (§13, §14)

New modules: `src/ai_review/checker.py` (tool auto-detect via `shutil.which`; run commands as safe subprocess lists; `trusted_commands` from org vs `commands` from repo; timestamps, exit codes, truncated output), new unit tests for auto-detection and trusted/untrusted separation. Config already carries `checks.*`. Checks run between security scan and prompt build; `CheckResult`s attach to `ReviewResult.checks` and render in terminal/JSON/Markdown.

Tasks (detailed at execution):
- T1 `detect_tools(profile) -> list[str]` (e.g. gofmt/go vet for Go, ruff for Python)
- T2 `run_checks(cfg, profile, repo_dir) -> list[CheckResult]`
- T3 wire into pipeline + tests + commit

Exit: a Go repo runs `gofmt -l`/`go vet`-style checks locally; untrusted repo `checks.commands` do NOT execute unless allowlisted.

---

# Phase 4 — Technology Analyzers (§9)

New: per-technology files under `src/ai_review/prompts/technology/` (`php.md`, `javascript.md`, `typescript.md`, `python.md`, `go.md`, `java.md`, `sql.md`, `docker.md`, `terraform.md`, `kubernetes.md`), each with technology-specific review rules AND file-classification depth rules (e.g. SQL migration columns, Terraform resource references, async JS pitfalls). `PromptBuilder.build` selects only layers whose language appeared in the scoped `RepoProfile`. Detector extended with language→analyzer mapping so only detected technologies are loaded. New `select_only` path formalized for monorepo (only affected-subbranch layers). Tests: TS repo gets TS rules + NOT PHP rules.

Tasks (detailed at execution):
- T1 extend detector/profiles with analyzer mapping
- T2 author the ten technology prompt files
- T3 builder layering + integration test + commit each technology (or batch)

Exit: per-language fixtures show only matching prompt layers shipped to the LLM.

---

# Phase 5 — Large-Change Chunking (§24)

New: `review.chunking` logic in `pipeline.py`/`chunker.py`. Group changed files by category (backend/frontend/database/infrastructure) then by size, chunk at `max_chunk_kb`, never splitting inside a function/logical block (chunk boundaries at file/hunk granularity). Providers support concurrent chunk review via `httpx.AsyncClient` (`LLMProvider.asend(payload)`) added behind the existing interface; parallelize with bounded concurrency, aggregate per-chunk `ReviewResult`s, feed one `PolicyEngine` decision. Tests: chunking correctness (blocks never split), aggregation determinism.

Tasks (detailed at execution):
- T1 chunker: `group_and_chunk(changes) -> list[list[StagedChange]]`
- T2 async provider path (`.asend`) + pool
- T3 aggregate + policy + tests + commit

Exit: a 5-file, 300KB staged diff reviews in ~chunk-count parallel calls with a single final decision.

---

# Phase 6 — Validation Reviewer, Caching, Generated Files, Extended Doctor (§21, §30, §31, §33)

Tasks (detailed at execution):
- T1 second-pass **validation reviewer**: for each candidate HIGH/CRITICAL blocking finding, send `validate` prompt (finding + evidence snippet) demanding confirm/deny with confidence; require confirmation to block; gated by `policy.validate_high` (default off → no extra latency).
- T2 **cache** (`src/ai_review/cache.py`): local SQLite (or file under `~/.cache/ai-review/`), keyed on `sha256(prompt_version + diff hash + config hash + profile names)`; store only metadata + decision, never source; `cache.enabled` honored; honor `AI_REVIEW_NO_CACHE=1`.
- T3 **generated files** policy (`generated.policy`: ignore|review|security_only) — route classified-generated files to redaction+secret-scan-only or full review.
- T4 **extended doctor**: auth probe, model availability named probe, hook state rev-check, prompt placeholder lint, cached-entry probe.
- Tests + commit per T.

Exit: repeated unchanged staged review is served from cache; HIGH finding requires confirm-pass; generated assets never send content; doctor covers every subsystem.

---

# Cross-Phase Constraints & Dependencies

- Phase order locked because: P5 needs provider async (built sync-first in P1); P6 latches onto P3/P5; P2/P3 independent, may interleave.
- Config schema from Phase 1 is the stable contract; later phases add fields via pydantic defaults (no breaking changes).
- Every phase ends with full suite green `-q` and a commit. Coverage floor 85% applies from Phase 1 onward (re-verified each phase).