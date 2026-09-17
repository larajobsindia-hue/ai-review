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


def _ctx(tmp_path, files, changes=None, languages=("Python",), frameworks=(),
         infrastructure=()):
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    cfg = AppConfig()
    cfg.static_analysis.tools = {name: StaticAnalysisToolConfig()
                                 for name in registry.REGISTRY}
    profile = RepoProfile(
        languages=[ProfileEntry(lang, 0.9, []) for lang in languages],
        frameworks=[ProfileEntry(fw, 0.9, []) for fw in frameworks],
        infrastructure=[ProfileEntry(name, 0.9, []) for name in infrastructure],
    )
    staged = changes if changes is not None else [
        StagedChange(path=name, status="modified") for name in files]
    return build_context(str(tmp_path), cfg, staged, profile)


def _analyzer(name):
    return registry.REGISTRY[name]()


def _run(analyzer, ctx, stdout="", stderr="", exit_code=0):
    from ai_review.static_analysis.runner import ToolRun
    return analyzer.parse(ToolRun(argv=["x"], status="run", exit_code=exit_code,
                                  stdout=stdout, stderr=stderr), ctx)


def _executable(tmp_path, rel):
    """Create an executable file (resolve() requires the +x bit)."""
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


# -- registry -------------------------------------------------------------------


def test_all_planned_analyzers_are_registered():
    from ai_review.static_analysis.analyzers import MODULES
    assert MODULES == (
        "semgrep", "phpstan", "eslint", "ruff", "staticcheck",
        "larastan", "typescript", "govet", "sqlfluff", "hadolint", "trivy",
        "codeql", "sonarqube", "checkov", "tflint", "kubeconform", "kube_linter",
    )
    for name in MODULES:
        assert registry.REGISTRY[name].name == name


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


# -- eslint ---------------------------------------------------------------------

ESLINT = _fixture("eslint.json")


def test_eslint_prefers_the_local_binary(tmp_path, monkeypatch):
    _executable(tmp_path, "node_modules/.bin/eslint")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/eslint")
    ctx = _ctx(tmp_path, {"src/app.ts": "let x = 1\n"}, languages=("TypeScript",))
    assert _analyzer("eslint").resolve(ctx) == str(tmp_path / "node_modules/.bin/eslint")
    assert _analyzer("eslint").local_paths == ("node_modules/.bin/eslint",)


def test_eslint_argv_pins_json_flags_and_targets(tmp_path):
    ctx = _ctx(tmp_path, {"src/app.ts": "let x = 1\n"}, languages=("TypeScript",))
    assert _analyzer("eslint").build_argv("/exe", ctx) == [
        "/exe", "--format", "json", "--no-color", "--no-error-on-unmatched-pattern",
        "src/app.ts"]
    assert _analyzer("eslint").ok_exit_codes == (0, 1)


def test_eslint_parses_security_warning_and_fatal(tmp_path):
    ctx = _ctx(tmp_path, {"src/app.ts": "let x = 1\n"}, languages=("TypeScript",))
    findings = _run(_analyzer("eslint"), ctx, stdout=ESLINT)
    by_rule = {f.rule_id: f for f in findings}
    security = by_rule["security/detect-eval-with-expression"]
    assert (security.severity, security.category) == ("HIGH", "SECURITY")
    assert security.file == "src/app.ts" and security.line == 12
    assert security.original_severity == "2"
    unused = by_rule["no-unused-vars"]
    assert unused.severity == "LOW" and unused.line == 20
    fatal = by_rule[None]
    assert fatal.severity == "MEDIUM" and "Parsing error" in fatal.original_message


def test_eslint_empty_output_is_a_clean_run(tmp_path):
    ctx = _ctx(tmp_path, {"src/app.ts": "let x = 1\n"}, languages=("TypeScript",))
    assert _run(_analyzer("eslint"), ctx, stdout="") == []


# -- ruff -----------------------------------------------------------------------

RUFF = _fixture("ruff.json")


def test_ruff_argv_pins_json_and_python_targets(tmp_path):
    ctx = _ctx(tmp_path, {"app/main.py": "x = 1\n", "app/types.pyi": "x: int\n"})
    argv = _analyzer("ruff").build_argv("/exe", ctx)
    assert argv[:6] == ["/exe", "check", "--output-format", "json", "--no-cache", "--quiet"]
    assert argv[-2:] == ["app/main.py", "app/types.pyi"]
    assert _analyzer("ruff").ok_exit_codes == (0, 1)


def test_ruff_parses_security_undefined_and_unknown_codes(tmp_path):
    ctx = _ctx(tmp_path, {"app/config.py": "x = 1\n", "app/main.py": "x = 1\n"})
    findings = _run(_analyzer("ruff"), ctx, stdout=RUFF)
    by_rule = {f.rule_id: f for f in findings}
    assert (by_rule["S105"].severity, by_rule["S105"].category) == ("HIGH", "SECURITY")
    assert by_rule["S105"].file == "app/config.py" and by_rule["S105"].line == 3
    assert (by_rule["F821"].severity, by_rule["F821"].category) == ("HIGH", "BUG")
    assert by_rule["E501"].severity == "LOW"
    assert _run(_analyzer("ruff"), ctx, stdout="") == []


# -- staticcheck ----------------------------------------------------------------

STATICCHECK = _fixture("staticcheck.ndjson")


def test_staticcheck_requires_go_mod_and_is_project_scope(tmp_path):
    analyzer = _analyzer("staticcheck")
    assert analyzer.languages == ("Go",)
    assert analyzer.scope == "project"
    assert analyzer.ok_exit_codes == (0, 1)
    without = _ctx(tmp_path, {"pkg/parse.go": "package p\n"}, languages=("Go",))
    assert analyzer.build_argv("/exe", without) == []
    with_mod = _ctx(tmp_path, {"go.mod": "module x\n", "pkg/parse.go": "package p\n"},
                    languages=("Go",))
    assert analyzer.build_argv("/exe", with_mod) == ["/exe", "-f", "json", "./..."]


def test_staticcheck_parses_ndjson_severity_and_st1_category(tmp_path):
    ctx = _ctx(tmp_path, {"go.mod": "module x\n", "pkg/parse.go": "package p\n"},
               languages=("Go",))
    findings = _run(_analyzer("staticcheck"), ctx, stdout=STATICCHECK)
    assert len(findings) == 2
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["SA1000"].severity == "HIGH" and by_rule["SA1000"].line == 31
    assert by_rule["SA1000"].file == "pkg/parse.go"
    assert by_rule["ST1005"].severity == "MEDIUM"
    assert by_rule["ST1005"].category == "MAINTAINABILITY"


# -- larastan -------------------------------------------------------------------


def test_larastan_gates_on_laravel_and_reuses_phpstan_parser(tmp_path):
    analyzer = _analyzer("larastan")
    assert analyzer.frameworks == ("Laravel",)
    assert analyzer.local_paths == ("vendor/bin/larastan",)
    php = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n"}, languages=("PHP",))
    laravel = _ctx(tmp_path, {"app/Services/OrderService.php": "<?php\n"},
                   languages=("PHP",), frameworks=("Laravel",))
    assert not analyzer.supports(php)
    assert analyzer.supports(laravel)
    assert analyzer.build_argv("/exe", laravel) == [
        "/exe", "analyse", "--error-format=json", "--no-progress"]
    findings = _run(analyzer, laravel, stdout=PHPSTAN)
    assert {f.rule_id for f in findings} == {
        "argument.type", "method.notFound", "missingType.return", None}
    assert all(f.tool == "larastan" for f in findings)


# -- typescript -----------------------------------------------------------------

TYPESCRIPT = _fixture("typescript.txt")


def test_typescript_requires_tsconfig_and_never_emits(tmp_path):
    analyzer = _analyzer("typescript")
    assert analyzer.languages == ("TypeScript",)
    assert analyzer.local_paths == ("node_modules/.bin/tsc",)
    assert analyzer.ok_exit_codes == (0, 1, 2)
    without = _ctx(tmp_path, {"src/app.ts": "let x = 1\n"}, languages=("TypeScript",))
    assert analyzer.build_argv("/exe", without) == []
    with_cfg = _ctx(tmp_path, {"tsconfig.json": "{}\n", "src/app.ts": "let x = 1\n"},
                    languages=("TypeScript",))
    assert analyzer.build_argv("/exe", with_cfg) == ["/exe", "--noEmit", "--pretty", "false"]


def test_typescript_parses_pretty_false_diagnostics(tmp_path):
    ctx = _ctx(tmp_path, {"tsconfig.json": "{}\n", "src/app.ts": "let x = 1\n"},
               languages=("TypeScript",))
    findings = _run(_analyzer("typescript"), ctx, stdout=TYPESCRIPT)
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["TS2322"].severity == "HIGH" and by_rule["TS2322"].line == 12
    assert by_rule["TS2322"].file == "src/app.ts"
    assert by_rule["TS6046"].severity == "LOW"
    assert by_rule["TS1005"].severity == "HIGH"


# -- govet ----------------------------------------------------------------------

GOVET = _fixture("govet.txt")


def test_govet_requires_go_mod_and_parses_stderr_text(tmp_path):
    analyzer = _analyzer("govet")
    assert analyzer.languages == ("Go",)
    assert analyzer.scope == "project"
    assert analyzer.ok_exit_codes == (0, 1)
    without = _ctx(tmp_path, {"pkg/parse.go": "package p\n"}, languages=("Go",))
    assert analyzer.build_argv("/usr/bin/go", without) == []
    with_mod = _ctx(tmp_path, {"go.mod": "module x\n", "pkg/parse.go": "package p\n"},
                    languages=("Go",))
    assert analyzer.build_argv("/usr/bin/go", with_mod) == ["/usr/bin/go", "vet", "./..."]
    findings = _run(analyzer, with_mod, stderr=GOVET)
    assert [f.severity for f in findings] == ["MEDIUM", "MEDIUM"]
    assert findings[0].file == "pkg/parse.go" and findings[0].line == 12
    assert "unreachable" in findings[0].original_message


# -- sqlfluff -------------------------------------------------------------------

SQLFLUFF = _fixture("sqlfluff.json")


def test_sqlfluff_only_targets_sql_and_maps_prefixes(tmp_path):
    analyzer = _analyzer("sqlfluff")
    assert analyzer.languages == ("SQL",)
    assert analyzer.extensions == (".sql",)
    ctx = _ctx(tmp_path, {"queries/users.sql": "SELECT 1;\n"}, languages=("SQL",))
    assert analyzer.build_argv("/exe", ctx) == [
        "/exe", "lint", "--format", "json", "queries/users.sql"]
    findings = _run(analyzer, ctx, stdout=SQLFLUFF)
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["L003"].severity == "MEDIUM"
    assert by_rule["LT01"].severity == "LOW"
    assert by_rule["PRS"].severity == "MEDIUM"
    assert by_rule["L003"].file == "queries/users.sql"


# -- hadolint -------------------------------------------------------------------

HADOLINT = _fixture("hadolint.json")


def test_hadolint_only_targets_dockerfiles(tmp_path):
    analyzer = _analyzer("hadolint")
    docker = _ctx(tmp_path, {"Dockerfile": "FROM alpine\n"}, infrastructure=("Docker",))
    other = _ctx(tmp_path, {"app/util.py": "x = 1\n"}, infrastructure=("Docker",))
    assert analyzer.supports(docker)
    assert not analyzer.supports(other)
    assert analyzer.build_argv("/exe", docker) == ["/exe", "--format", "json", "Dockerfile"]
    findings = _run(analyzer, docker, stdout=HADOLINT)
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["DL3008"].severity == "MEDIUM"
    assert by_rule["DL3002"].severity == "HIGH"
    assert by_rule["DL3006"].severity == "LOW"


# -- trivy ----------------------------------------------------------------------

TRIVY = _fixture("trivy.json")


def test_trivy_never_downloads_a_database(tmp_path):
    analyzer = _analyzer("trivy")
    ctx = _ctx(tmp_path, {"Dockerfile": "FROM alpine\n"}, infrastructure=("Docker",))
    argv = analyzer.build_argv("/exe", ctx)
    assert argv == ["/exe", "config", "--format", "json", "--scanners", "misconfig",
                    "--skip-db-update", "."]
    assert "--skip-db-update" in argv
    findings = _run(analyzer, ctx, stdout=TRIVY)
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["DS002"].severity == "HIGH" and by_rule["DS002"].line == 10
    assert by_rule["DS026"].severity == "LOW"


# -- codeql / sonarqube ---------------------------------------------------------


def test_codeql_never_runs_without_a_database(tmp_path, monkeypatch):
    analyzer = _analyzer("codeql")
    ctx = _ctx(tmp_path, {"app/util.py": "x = 1\n"})
    monkeypatch.setattr(analyzer, "resolve", lambda ctx: "/bin/true")
    result = analyzer.analyze(ctx)
    assert result.status == "skipped" and result.findings == []
    assert "database" in result.error
    assert analyzer.build_argv("/exe", ctx) == []


def test_sonarqube_never_contacts_a_server(tmp_path, monkeypatch):
    analyzer = _analyzer("sonarqube")
    ctx = _ctx(tmp_path, {"app/util.py": "x = 1\n"})
    monkeypatch.setattr(analyzer, "resolve", lambda ctx: "/bin/true")
    result = analyzer.analyze(ctx)
    assert result.status == "skipped" and result.findings == []
    assert "server" in result.error


# -- checkov / tflint / kubeconform / kube-linter -------------------------------

CHECKOV = _fixture("checkov.json")
TFLINT = _fixture("tflint.json")
KUBECONFORM = _fixture("kubeconform.json")
KUBE_LINTER = _fixture("kube_linter.json")


def test_checkov_gates_on_terraform_or_kubernetes(tmp_path):
    analyzer = _analyzer("checkov")
    python = _ctx(tmp_path, {"app/util.py": "x = 1\n"})
    tf = _ctx(tmp_path, {"infra/s3.tf": "resource \"aws_s3_bucket\" \"b\" {}\n"},
              languages=(), infrastructure=("Terraform",))
    assert not analyzer.supports(python)
    assert analyzer.supports(tf)
    assert analyzer.build_argv("/exe", tf) == ["/exe", "-d", ".", "-o", "json", "--compact"]
    findings = _run(analyzer, tf, stdout=CHECKOV)
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["CKV_AWS_20"].severity == "HIGH"
    assert by_rule["CKV_AWS_20"].file == "infra/s3.tf"
    assert by_rule["CKV_K8S_21"].severity == "LOW"


def test_tflint_parses_issues_and_requires_terraform(tmp_path):
    analyzer = _analyzer("tflint")
    ctx = _ctx(tmp_path, {"main.tf": "resource \"aws_instance\" \"x\" {}\n"},
               languages=(), infrastructure=("Terraform",))
    assert analyzer.build_argv("/exe", ctx) == ["/exe", "--format", "json"]
    findings = _run(analyzer, ctx, stdout=TFLINT)
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["aws_instance_invalid_type"].severity == "HIGH"
    assert by_rule["aws_instance_invalid_type"].line == 8
    assert by_rule["terraform_naming_convention"].severity == "MEDIUM"


def test_kubeconform_skips_valid_resources(tmp_path):
    analyzer = _analyzer("kubeconform")
    ctx = _ctx(tmp_path, {"k8s/deployment.yaml": "apiVersion: apps/v1\n",
                          "k8s/service.yaml": "apiVersion: v1\n"},
               languages=(), infrastructure=("Kubernetes",))
    assert analyzer.build_argv("/exe", ctx) == [
        "/exe", "-output", "json", "k8s/deployment.yaml", "k8s/service.yaml"]
    findings = _run(analyzer, ctx, stdout=KUBECONFORM)
    assert len(findings) == 1
    assert findings[0].file == "k8s/deployment.yaml"
    assert findings[0].severity == "HIGH"
    assert "containrs" in findings[0].original_message


def test_kube_linter_parses_reports(tmp_path):
    analyzer = _analyzer("kube_linter")
    ctx = _ctx(tmp_path, {"k8s/deployment.yaml": "apiVersion: apps/v1\n"},
               languages=(), infrastructure=("Kubernetes",))
    assert analyzer.build_argv("/exe", ctx) == [
        "/exe", "lint", "--format", "json", "k8s/deployment.yaml"]
    findings = _run(analyzer, ctx, stdout=KUBE_LINTER)
    assert [f.rule_id for f in findings] == ["no-read-only-root-fs", "unset-cpu-requirements"]
    assert all(f.file == "k8s/deployment.yaml" for f in findings)
    assert all(f.severity == "MEDIUM" for f in findings)
