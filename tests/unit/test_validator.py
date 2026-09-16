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
