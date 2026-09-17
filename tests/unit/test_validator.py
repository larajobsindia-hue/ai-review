from ai_review.models import Finding, Hunk, ReviewResult, StagedChange
from ai_review.validator import validate_findings


def _finding(file="a.go", line=3, severity="HIGH", confidence=0.9):
    return Finding(severity=severity, category="BUG", file=file, line=line,
                   title="t", description="d", evidence="e", recommendation="r",
                   confidence=confidence)


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
    assert out.decision == "BLOCK"


def test_keeps_hard_block_finding_even_without_line():
    change = StagedChange(path="a.go", status="added", hunks=[Hunk(1, 2, 1, 2, changed_new_lines={1})])
    f = _finding("a.go", None)
    f.hard_block = True
    result = ReviewResult(decision="BLOCK", summary="s", issues=[f])
    out = validate_findings(result, [change])
    assert len(out.issues) == 1


def test_drops_invalid_severity():
    change = StagedChange(path="a.go", status="modified", hunks=[Hunk(1, 5, 1, 5, changed_new_lines={3})])
    result = ReviewResult(decision="BLOCK", summary="s", issues=[_finding("a.go", 3, severity="NOPE")])
    out = validate_findings(result, [change])
    assert out.issues == []


def test_drops_confidence_above_one():
    change = StagedChange(path="a.go", status="modified", hunks=[Hunk(1, 5, 1, 5, changed_new_lines={3})])
    result = ReviewResult(decision="BLOCK", summary="s", issues=[_finding("a.go", 3, confidence=1.5)])
    out = validate_findings(result, [change])
    assert out.issues == []


def test_drops_negative_confidence():
    change = StagedChange(path="a.go", status="modified", hunks=[Hunk(1, 5, 1, 5, changed_new_lines={3})])
    result = ReviewResult(decision="BLOCK", summary="s", issues=[_finding("a.go", 3, confidence=-0.1)])
    out = validate_findings(result, [change])
    assert out.issues == []


def test_drops_line_none_without_hard_block():
    change = StagedChange(path="a.go", status="modified", hunks=[Hunk(1, 5, 1, 5, changed_new_lines={3})])
    result = ReviewResult(decision="WARN", summary="s", issues=[_finding("a.go", None)])
    out = validate_findings(result, [change])
    assert out.issues == []


def test_drops_wrong_line_in_staged_file():
    change = StagedChange(path="a.go", status="modified", hunks=[Hunk(1, 5, 1, 5, changed_new_lines={3})])
    result = ReviewResult(decision="BLOCK", summary="s", issues=[_finding("a.go", 4)])
    out = validate_findings(result, [change])
    assert out.issues == []


def test_drops_hard_block_finding_when_file_unstaged():
    change = StagedChange(path="b.go", status="added", hunks=[Hunk(1, 2, 1, 2, changed_new_lines={1})])
    f = _finding("a.go", None)
    f.hard_block = True
    result = ReviewResult(decision="BLOCK", summary="s", issues=[f])
    out = validate_findings(result, [change])
    assert out.issues == []


def test_downgrades_block_when_no_blocking_finding_kept():
    change = StagedChange(path="a.go", status="modified", hunks=[Hunk(1, 5, 1, 5, changed_new_lines={3})])
    result = ReviewResult(decision="BLOCK", summary="s", issues=[_finding("a.go", 3, severity="MEDIUM")])
    out = validate_findings(result, [change])
    assert len(out.issues) == 1
    assert out.decision == "WARN"


def test_keeps_block_when_hard_block_kept():
    change = StagedChange(path="a.go", status="added", hunks=[Hunk(1, 2, 1, 2, changed_new_lines={1})])
    f = _finding("a.go", None)
    f.hard_block = True
    result = ReviewResult(decision="BLOCK", summary="s", issues=[f])
    out = validate_findings(result, [change])
    assert out.decision == "BLOCK"


from ai_review.models import (StaticAnalysisSummary, StaticAssessment, ToolResult)


def _static_finding(finding_id):
    return Finding(severity="LOW", category="BUG", file="a.go", line=3, title="s",
                   description="d", evidence="e", recommendation="r", confidence=0.6,
                   source="static_analysis", id=finding_id, tool="semgrep")


def _static_result(link="abc123",
                   assessments=(("abc123", "confirmed"), ("nope", "uncertain"))):
    change = StagedChange(path="a.go", status="modified",
                          hunks=[Hunk(1, 5, 1, 5, changed_new_lines={3})])
    issue = _finding("a.go", 3)
    issue.related_static_finding_id = link
    result = ReviewResult(
        decision="WARN", summary="s", issues=[issue],
        static=StaticAnalysisSummary(findings=[_static_finding("abc123")],
                                     tools=[ToolResult(name="semgrep", status="run")]),
        static_assessments=[StaticAssessment(finding_id=fid, verdict=verdict)
                            for fid, verdict in assessments])
    return result, [change]


def test_unknown_static_link_is_dropped_but_the_issue_survives():
    result, changes = _static_result(link="ghost")
    out = validate_findings(result, changes)
    assert out.issues[0].related_static_finding_id is None
    assert len(out.issues) == 1


def test_known_static_link_survives():
    result, changes = _static_result()
    assert validate_findings(result, changes).issues[0].related_static_finding_id == \
        "abc123"


def test_unknown_assessments_are_dropped():
    result, changes = _static_result()
    out = validate_findings(result, changes)
    assert [a.finding_id for a in out.static_assessments] == ["abc123"]


def test_links_are_cleared_when_static_analysis_is_off():
    result, changes = _static_result()
    result.static = None
    out = validate_findings(result, changes)
    assert out.issues[0].related_static_finding_id is None
    assert out.static_assessments == []


def test_static_reference_validation_does_not_change_the_decision():
    result, changes = _static_result(link="ghost")
    out = validate_findings(result, changes)
    assert out.decision == "WARN"


def test_multiple_hunks_union_changed_lines():
    change = StagedChange(path="a.go", status="modified", hunks=[
        Hunk(1, 5, 1, 5, changed_new_lines={3}),
        Hunk(10, 5, 10, 5, changed_new_lines={12}),
    ])
    result = ReviewResult(decision="WARN", summary="s", issues=[
        _finding("a.go", 3, severity="LOW"),
        _finding("a.go", 12, severity="LOW"),
    ])
    out = validate_findings(result, [change])
    assert len(out.issues) == 2
