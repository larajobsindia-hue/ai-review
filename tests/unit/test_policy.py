from ai_review.config import FailurePolicyConfig, PolicyConfig
from ai_review.models import Finding, ReviewResult
from ai_review.policy import FailureDecision, PolicyEngine


def _f(sev, conf, pre=False, hard=False, file="a.go"):
    f = Finding(severity=sev, category="BUG", file=file, line=1, title="t",
                description="d", evidence="e", recommendation="r", confidence=conf)
    f.is_pre_existing = pre
    f.hard_block = hard
    return f


def test_block_high_confidence():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[_f("HIGH", 0.9)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "BLOCK"


def test_no_block_below_confidence_threshold():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[_f("HIGH", 0.5)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "WARN"


def test_no_block_pre_existing():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[_f("HIGH", 0.9, pre=True)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "WARN"


def test_hard_block_wins():
    r = ReviewResult(decision="PASS", summary="s", issues=[_f("INFO", 0.2, hard=True)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "BLOCK"


def test_pass_with_no_issues():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "PASS"
    assert out.summary == "No issues found."


def test_critical_blocks():
    r = ReviewResult(decision="WARN", summary="s", issues=[_f("CRITICAL", 0.9)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "BLOCK"


def test_exact_threshold_boundary_blocks():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[_f("HIGH", 0.80)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "BLOCK"


def test_hard_block_without_file_does_not_block():
    r = ReviewResult(decision="PASS", summary="s", issues=[_f("INFO", 0.2, hard=True, file=None)])
    out = PolicyEngine(PolicyConfig()).decide(r)
    assert out.decision == "WARN"


def test_custom_block_on_high_does_not_block():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[_f("HIGH", 0.99)])
    out = PolicyEngine(PolicyConfig(block_on=["CRITICAL"], minimum_confidence_to_block=0.80)).decide(r)
    assert out.decision == "WARN"


def test_custom_block_on_critical_blocks():
    r = ReviewResult(decision="BLOCK", summary="s", issues=[_f("CRITICAL", 0.99)])
    out = PolicyEngine(PolicyConfig(block_on=["CRITICAL"], minimum_confidence_to_block=0.80)).decide(r)
    assert out.decision == "BLOCK"


def test_failure_warn():
    out = FailureDecision().apply("warn", "LLM down")
    assert out.decision == "WARN"
    assert "AI review was skipped" in out.summary


def test_failure_allow_is_pass_not_passed_claim():
    out = FailureDecision().apply("allow", "LLM down")
    assert out.decision == "PASS"
    assert "AI review passed" not in out.summary.lower()


def test_failure_block():
    out = FailureDecision().apply("block", "LLM down")
    assert out.decision == "BLOCK"


def test_failure_summaries_start_with_message_and_state_outcome():
    for policy, decision in (("block", "BLOCK"), ("warn", "WARN"), ("allow", "PASS")):
        out = FailureDecision().apply(policy, "LLM down")
        assert out.decision == decision
        assert out.summary.startswith("AI review was skipped. LLM down")
        assert f"failure_policy={policy}" in out.summary


def test_failure_unknown_defaults_to_warn():
    assert FailurePolicyConfig().on_llm_unavailable == "warn"
    out = FailureDecision().apply("bogus", "LLM down")
    assert out.decision == "WARN"
    assert out.summary.startswith("AI review was skipped. LLM down")
