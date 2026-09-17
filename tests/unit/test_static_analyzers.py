"""Per-analyzer argv + parser tests, driven by checked-in tool fixtures.

No test here needs an installed analyzer: argv construction is pure, and parsing
is fed a recorded JSON payload through ``analyzer.parse``. The one place a real
process would be involved (``analyze``) stubs the runner (upstream §32/§33).
"""
import json
from pathlib import Path

import pytest

from ai_review.config import AppConfig, StaticAnalysisToolConfig
from ai_review.models import ProfileEntry, RepoProfile, StagedChange
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
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
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig()
                                 for name in registry.REGISTRY}
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


def _executable(tmp_path, rel):
    """Create an executable file (resolve() requires the +x bit)."""
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


# -- registry -------------------------------------------------------------------


def test_both_priority_one_analyzers_are_registered():
    assert registry.REGISTRY["semgrep"].name == "semgrep"
    assert registry.REGISTRY["phpstan"].name == "phpstan"


# -- semgrep --------------------------------------------------------------------

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


def test_semgrep_finds_a_config_under_the_semgrep_directory(tmp_path):
    ctx = _ctx(tmp_path, {".semgrep/rules.yml": "rules: []\n",
                          "app/util.py": "x = 1\n"})
    argv = _analyzer("semgrep").build_argv("/usr/bin/semgrep", ctx)
    assert argv[argv.index("--config") + 1] == ".semgrep/rules.yml"


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


def test_semgrep_is_always_evidence_never_a_gate(tmp_path):
    ctx = _ctx(tmp_path, {"semgrep.yml": "rules: []\n", "app/Models/User.php": "<?php\n"})
    for finding in _run(_analyzer("semgrep"), ctx, stdout=SEMGREP):
        assert finding.source == "static_analysis"
        assert finding.hard_block is False
        assert finding.tool == "semgrep"
        assert finding.detected_by == ["semgrep"]


def test_semgrep_ok_exit_codes_treat_findings_as_success():
    assert _analyzer("semgrep").ok_exit_codes == (0, 1)


def test_semgrep_empty_output_is_a_clean_run(tmp_path):
    ctx = _ctx(tmp_path, {"semgrep.yml": "rules: []\n", "app/util.py": "x = 1\n"})
    assert _run(_analyzer("semgrep"), ctx, stdout="") == []


def test_semgrep_malformed_output_fails_the_tool_not_the_review(tmp_path, monkeypatch):
    from ai_review.static_analysis import runner
    ctx = _ctx(tmp_path, {"semgrep.yml": "rules: []\n", "app/util.py": "x = 1\n"})
    # resolve() too: no analyzer is installed in this environment, and the point
    # of the test is what happens *after* a tool has run and printed garbage.
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(argv=list(argv), status="run",
                                                          exit_code=0, stdout="not json"))
    result = _analyzer("semgrep").analyze(ctx)
    assert result.status == "failed" and result.findings == []


def test_semgrep_normalizes_absolute_tool_paths(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/util.py").write_text("x = 1\n")
    body = json.dumps({"results": [{
        "check_id": "python.lang.correctness.useless-eqeq",
        "path": str(tmp_path / "app/util.py"), "start": {"line": 7},
        "extra": {"message": "Comparison is always false.", "severity": "WARNING",
                  "metadata": {}}}]})
    ctx = _ctx(tmp_path, {"app/util.py": "x = 1\n"})
    assert _run(_analyzer("semgrep"), ctx, stdout=body)[0].file == "app/util.py"


# -- phpstan --------------------------------------------------------------------

PHPSTAN = _fixture("phpstan.json")


def test_phpstan_prefers_the_vendored_binary(tmp_path, monkeypatch):
    _executable(tmp_path, "vendor/bin/phpstan")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/phpstan")
    ctx = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n"}, languages=("PHP",))
    assert _analyzer("phpstan").resolve(ctx) == str(tmp_path / "vendor/bin/phpstan")


def test_phpstan_falls_back_to_path(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/phpstan")
    ctx = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n"}, languages=("PHP",))
    assert _analyzer("phpstan").resolve(ctx) == "/usr/local/bin/phpstan"


def test_phpstan_points_at_changed_files_without_a_config(tmp_path):
    ctx = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n"}, languages=("PHP",))
    assert _analyzer("phpstan").build_argv("/exe", ctx) == [
        "/exe", "analyse", "--error-format=json", "--no-progress",
        "--no-interaction", "app/Services/OrderService.php"]


def test_phpstan_defers_to_a_project_config(tmp_path):
    ctx = _ctx(tmp_path, {"phpstan.neon": "parameters: {}\n",
                          "app/Services/OrderService.php": "<?php\n"}, languages=("PHP",))
    assert _analyzer("phpstan").build_argv("/exe", ctx) == [
        "/exe", "analyse", "--error-format=json", "--no-progress", "--no-interaction"]


def test_phpstan_is_empty_without_php_targets(tmp_path):
    ctx = _ctx(tmp_path, {"app/util.py": "x = 1\n"}, languages=("PHP",))
    assert _analyzer("phpstan").build_argv("/exe", ctx) == []


def test_phpstan_only_applies_to_php_projects(tmp_path):
    php = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n"}, languages=("PHP",))
    python = _ctx(tmp_path, {"app/util.py": "x = 1\n"}, languages=("Python",))
    assert _analyzer("phpstan").supports(php)
    assert not _analyzer("phpstan").supports(python)


def test_phpstan_ok_exit_codes_treat_errors_as_success():
    assert _analyzer("phpstan").ok_exit_codes == (0, 1)


@pytest.mark.parametrize("identifier,expected", [
    ("argument.type", "HIGH"),
    ("method.nonObject", "HIGH"),
    ("property.nonObject", "HIGH"),
    ("variable.undefined", "HIGH"),
    ("class.notFound", "HIGH"),
    ("function.notFound", "HIGH"),
    ("method.notFound", "HIGH"),
    ("return.type", "HIGH"),
    ("binaryOp.invalid", "HIGH"),
    ("offsetAccess.nonOffsetAccessible", "HIGH"),
    ("missingType.return", "LOW"),
    ("missingReturn.", "LOW"),
    ("deadCode.unreachable", "LOW"),
    ("unusedParam.neverRead", "LOW"),
    ("deprecated.method", "LOW"),
    ("some.future.rule", "MEDIUM"),
    (None, "MEDIUM"),
])
def test_phpstan_identifier_severity_mapping(identifier, expected):
    from ai_review.static_analysis.analyzers.phpstan import severity_for
    assert severity_for(identifier) == expected


def test_phpstan_parses_files_identifiers_and_lines(tmp_path):
    ctx = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n",
                          "app/Models/User.php": "<?php\n"}, languages=("PHP",))
    findings = _run(_analyzer("phpstan"), ctx, stdout=PHPSTAN)
    by_rule = {f.rule_id: f for f in findings}
    assert set(by_rule) == {"argument.type", "method.notFound",
                            "missingType.return", None}
    wrong_type = by_rule["argument.type"]
    assert wrong_type.severity == "HIGH" and wrong_type.category == "BUG"
    assert wrong_type.file == "app/Services/OrderService.php"
    assert wrong_type.line == 142
    assert wrong_type.tool == "phpstan"
    assert wrong_type.original_severity is None        # PHPStan has no severities
    assert "Parameter #1 $id" in wrong_type.original_message
    assert by_rule["method.notFound"].severity == "HIGH"
    assert by_rule["missingType.return"].severity == "LOW"
    unlabelled = by_rule[None]
    assert unlabelled.severity == "MEDIUM" and unlabelled.line == 30


def test_phpstan_parses_in_sorted_file_order(tmp_path):
    ctx = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n",
                          "app/Models/User.php": "<?php\n"}, languages=("PHP",))
    files = [f.file for f in _run(_analyzer("phpstan"), ctx, stdout=PHPSTAN)]
    assert files == sorted(files)


def test_phpstan_normalizes_absolute_tool_paths(tmp_path):
    target = tmp_path / "app/Services/OrderService.php"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<?php\n")
    body = json.dumps({"totals": {"errors": 1, "file_errors": 1}, "files": {
        str(target): {"errors": 1, "messages": [
            {"message": "Method missing() not found.", "line": 3,
             "identifier": "method.notFound"}]}}, "errors": []})
    ctx = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n"}, languages=("PHP",))
    findings = _run(_analyzer("phpstan"), ctx, stdout=body)
    assert [f.file for f in findings] == ["app/Services/OrderService.php"]


def test_phpstan_empty_output_is_a_clean_run(tmp_path):
    ctx = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n"}, languages=("PHP",))
    assert _run(_analyzer("phpstan"), ctx, stdout="") == []


def test_phpstan_malformed_output_fails_the_tool_not_the_review(tmp_path, monkeypatch):
    from ai_review.static_analysis import runner
    ctx = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n"}, languages=("PHP",))
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: "/bin/true")
    monkeypatch.setattr("ai_review.static_analysis.base.run_tool",
                        lambda argv, **kw: runner.ToolRun(argv=list(argv), status="run",
                                                          exit_code=0, stdout="<html>"))
    result = _analyzer("phpstan").analyze(ctx)
    assert result.status == "failed" and result.findings == []


def test_phpstan_not_installed_is_unavailable_not_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(StaticAnalyzer, "resolve", lambda self, ctx: None)
    ctx = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n"}, languages=("PHP",))
    result = _analyzer("phpstan").analyze(ctx)
    assert result.status == "unavailable" and result.findings == []
