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