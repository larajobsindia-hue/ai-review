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
    # Snapshot the org layer's directives BEFORE merging: lower layers
    # (user/repo/CLI) must not be able to weaken or disable them.
    org_layer = _load_yaml(org_yaml) if isinstance(org_yaml, str) else org_yaml
    org_directives: dict = {}
    if isinstance(org_layer, dict):
        directives = org_layer.get("organization")
        if isinstance(directives, dict):
            org_directives = directives
    org_enforce = bool(org_directives.get("enforce", False))
    org_redact = org_directives.get("redact_secrets")

    merged: dict = {}
    for candidate in (defaults_yaml, org_yaml, user_yaml, repo_yaml):
        layer = _load_yaml(candidate) if isinstance(candidate, str) else candidate
        if layer:
            merged = merge_dict(merged, layer)
    if cli_overrides:
        merged = merge_dict(merged, cli_overrides)

    cfg = AppConfig.model_validate(merged)

    # Organization enforcement: repo/user/CLI layers cannot weaken mandatory
    # rules (they may only tighten); enforcement applies from the org snapshot.
    if org_enforce:
        if org_redact is not False:
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