from pathlib import Path

from ai_review.config import load_config, merge_dict

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


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_merge_dict_recursive():
    base = {"a": {"x": 1}, "b": 2}
    overlay = {"a": {"y": 2}, "b": 3, "c": 4}
    out = merge_dict(base, overlay)
    assert out == {"a": {"x": 1, "y": 2}, "b": 3, "c": 4}


def test_merge_dict_list_replacement():
    base = {"a": [1, 2]}
    overlay = {"a": [3]}
    out = merge_dict(base, overlay)
    assert out == {"a": [3]}


def test_load_config_defaults(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML)
    cfg = load_config(
        defaults_yaml=defaults, org_yaml=None, user_yaml=None,
        repo_yaml=None, cli_overrides=None, repo_dir=str(tmp_path),
    )
    assert cfg.llm.endpoint == "http://127.0.0.1:8080"
    assert cfg.policy.minimum_confidence_to_block == 0.80
    assert cfg.policy.block_on == ["CRITICAL", "HIGH"]


def test_org_enforce_blocks_redact_weakening(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML)
    org_yaml = _write(tmp_path, "org.yaml", "organization:\n  enforce: true\n")
    repo_yaml = _write(tmp_path, "repo.yaml", "security:\n  redact_secrets: false\n")
    cfg = load_config(
        defaults_yaml=defaults, org_yaml=org_yaml, user_yaml=None,
        repo_yaml=repo_yaml, cli_overrides=None, repo_dir=str(tmp_path),
    )
    assert cfg.organization.enforce is True
    assert cfg.security.redact_secrets is True


def test_cli_overrides_win(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML)
    cfg = load_config(
        defaults_yaml=defaults, org_yaml=None, user_yaml=None,
        repo_yaml=None, cli_overrides={"llm": {"endpoint": "http://x:9"}},
        repo_dir=str(tmp_path),
    )
    assert cfg.llm.endpoint == "http://x:9"


def test_precedence_cli_wins(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML)
    org_yaml = _write(tmp_path, "org.yaml", "llm:\n  endpoint: http://org:1\n")
    user_yaml = _write(tmp_path, "user.yaml", "llm:\n  endpoint: http://user:2\n")
    repo_yaml = _write(tmp_path, "repo.yaml", "llm:\n  endpoint: http://repo:3\n")
    cfg = load_config(
        defaults_yaml=defaults, org_yaml=org_yaml, user_yaml=user_yaml,
        repo_yaml=repo_yaml, cli_overrides={"llm": {"endpoint": "http://cli:4"}},
        repo_dir=str(tmp_path),
    )
    assert cfg.llm.endpoint == "http://cli:4"


def test_precedence_repo_beats_user_beats_org(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML)
    org_yaml = _write(tmp_path, "org.yaml", "llm:\n  endpoint: http://org:1\n")
    user_yaml = _write(tmp_path, "user.yaml", "llm:\n  endpoint: http://user:2\n")
    repo_yaml = _write(tmp_path, "repo.yaml", "llm:\n  endpoint: http://repo:3\n")
    cfg = load_config(
        defaults_yaml=defaults, org_yaml=org_yaml, user_yaml=user_yaml,
        repo_yaml=repo_yaml, cli_overrides=None, repo_dir=str(tmp_path),
    )
    assert cfg.llm.endpoint == "http://repo:3"


def test_cli_redact_forced_true_under_org_enforcement(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML)
    org_yaml = _write(tmp_path, "org.yaml", "organization:\n  enforce: true\n")
    repo_yaml = _write(tmp_path, "repo.yaml", "security:\n  redact_secrets: false\n")
    cfg = load_config(
        defaults_yaml=defaults, org_yaml=org_yaml, user_yaml=None,
        repo_yaml=repo_yaml, cli_overrides={"security": {"redact_secrets": False}},
        repo_dir=str(tmp_path),
    )
    assert cfg.security.redact_secrets is True


def test_repo_cannot_disable_org_enforcement(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML)
    org_yaml = _write(
        tmp_path, "org.yaml",
        "organization:\n  enforce: true\n  redact_secrets: true\n",
    )
    repo_yaml = _write(
        tmp_path, "repo.yaml",
        "organization:\n  enforce: false\nsecurity:\n  redact_secrets: false\n",
    )
    cfg = load_config(
        defaults_yaml=defaults, org_yaml=org_yaml, user_yaml=None,
        repo_yaml=repo_yaml, cli_overrides=None, repo_dir=str(tmp_path),
    )
    assert cfg.security.redact_secrets is True


def test_cli_cannot_disable_org_enforcement(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML)
    org_yaml = _write(tmp_path, "org.yaml", "organization:\n  enforce: true\n")
    cfg = load_config(
        defaults_yaml=defaults, org_yaml=org_yaml, user_yaml=None,
        repo_yaml=None,
        cli_overrides={"organization": {"enforce": False}},
        repo_dir=str(tmp_path),
    )
    assert cfg.security.redact_secrets is True


def test_no_org_file_repo_may_disable_redact(tmp_path):
    defaults = _write(tmp_path, "default.yaml", DEFAULT_YAML)
    repo_yaml = _write(
        tmp_path, "repo.yaml",
        "organization:\n  enforce: false\nsecurity:\n  redact_secrets: false\n",
    )
    cfg = load_config(
        defaults_yaml=defaults, org_yaml=None, user_yaml=None,
        repo_yaml=repo_yaml, cli_overrides=None, repo_dir=str(tmp_path),
    )
    assert cfg.organization.enforce is False
    assert cfg.security.redact_secrets is False
