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