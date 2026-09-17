"""The static evidence block reaches the LLM prompt in the documented shape."""
from ai_review.models import (RepoProfile, StagedChange, StaticAnalysisSummary,
                              ToolResult)
from ai_review.prompts import OUTPUT_JSON_SCHEMA, PromptBuilder, default_prompt_dir
from ai_review.static_analysis import format_static_findings
from ai_review.static_analysis.normalizer import make_finding


def _payload(static_text=""):
    builder = PromptBuilder(default_prompt_dir())
    return builder.build(profile=RepoProfile(),
                         changes=[StagedChange(path="a.py", status="modified")],
                         diff_text="+x = 1\n", context_text="", static_text=static_text)


def test_static_section_is_added_when_evidence_exists():
    finding = make_finding(tool="semgrep", severity="HIGH",
                           rule_id="php.security.sql-injection",
                           file="app/Models/User.php", line=42,
                           message="Potential SQL injection.")
    summary = StaticAnalysisSummary(findings=[finding],
                                    tools=[ToolResult(name="semgrep", status="run")])
    payload = _payload(format_static_findings(summary))
    assert "# Static Analysis Findings" in payload.user
    assert "app/Models/User.php:42" in payload.user
    assert f"id {finding.id}" in payload.user
    assert payload.user.index("# Static Analysis Findings") < \
        payload.user.index("# Output Format")


def test_static_section_is_absent_when_there_is_no_evidence():
    assert "# Static Analysis Findings" not in _payload("").user
    assert "# Static Analysis Findings" not in _payload("   ").user


def test_existing_prompt_sections_are_unchanged():
    user = _payload().user
    for section in ("# Staged Files", "# Staged Diff", "# Relevant Context",
                    "# Output Format"):
        assert section in user


def test_schema_exposes_static_assessments_and_related_ids():
    issue = OUTPUT_JSON_SCHEMA["properties"]["issues"]["items"]["properties"]
    assert issue["related_static_finding_id"]["type"] == ["string", "null"]
    assert "related_static_finding_id" not in \
        OUTPUT_JSON_SCHEMA["properties"]["issues"]["items"]["required"]
    assessments = OUTPUT_JSON_SCHEMA["properties"]["static_assessments"]
    assert assessments["items"]["properties"]["verdict"]["enum"] == [
        "confirmed", "likely_true", "uncertain", "likely_false_positive", "false_positive"]
    assert OUTPUT_JSON_SCHEMA["required"] == ["decision", "summary", "issues"]


def test_system_prompt_tells_the_model_evidence_is_not_truth():
    system = _payload().system
    assert "EVIDENCE, not as truth" in system
    assert "static_assessments" in system
