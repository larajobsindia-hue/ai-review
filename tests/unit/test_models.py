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


from ai_review.models import (AnalyzerResult, StaticAnalysisSummary,
                              StaticAssessment, ToolResult)


def _finding(**kw):
    base = dict(severity="HIGH", category="BUG", file="a.go", line=4, title="t",
                description="d", evidence="e", recommendation="r", confidence=0.9)
    base.update(kw)
    return Finding(**base)


def test_finding_static_provenance_defaults_keep_ai_shape():
    f = _finding()
    assert f.source == "ai"
    assert f.id is None and f.tool is None and f.rule_id is None
    assert f.fingerprint is None
    assert f.original_severity is None and f.original_message is None
    assert f.detected_by == [] and f.related_static_finding_id is None


def test_static_summary_severity_counts_are_complete():
    summary = StaticAnalysisSummary(findings=[_finding(severity="HIGH"),
                                             _finding(severity="LOW")])
    assert summary.severity_counts() == {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 0,
                                         "LOW": 1, "INFO": 0}


def test_review_result_static_defaults_are_off():
    r = ReviewResult(decision="PASS", summary="s")
    assert r.static is None
    assert r.static_assessments == []


def test_tool_and_analyzer_result_defaults():
    t = ToolResult(name="ruff", status="run")
    assert (t.exit_code, t.error, t.duration_ms) == (None, "", 0)
    a = AnalyzerResult()
    assert a.findings == [] and a.status == "run" and a.exit_code is None


def test_static_assessment_shape():
    a = StaticAssessment(finding_id="abc123", verdict="likely_false_positive",
                         reason="guarded above")
    assert a.reason == "guarded above"
