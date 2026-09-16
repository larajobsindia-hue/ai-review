"""Policy engine: the LLM proposes, the policy disposes."""
from __future__ import annotations

from ai_review.config import PolicyConfig
from ai_review.models import Finding, ReviewResult


class PolicyEngine:
    """Sets the final decision; the LLM's declared decision alone never blocks."""

    def __init__(self, cfg: PolicyConfig):
        self.cfg = cfg
        self._block_on = set(cfg.block_on)

    def decide(self, result: ReviewResult) -> ReviewResult:
        """Compute the final decision from the validated issues only.

        The incoming ``result.decision`` is ignored: BLOCK when any finding
        passes :meth:`_blocks`, WARN when non-blocking issues remain, PASS
        when none do. On BLOCK the summary is replaced with a policy-authored
        message (the LLM's prose summary is not trusted for the gate); WARN
        preserves it.
        """
        if any(self._blocks(f) for f in result.issues):
            result.decision = "BLOCK"
            result.summary = "Blocking findings were validated against the staged changes."
            return result
        if result.issues:
            result.decision = "WARN"
        else:
            result.decision = "PASS"
            result.summary = "No issues found."
        return result

    def _blocks(self, finding: Finding) -> bool:
        if finding.hard_block:
            return finding.file is not None
        if finding.is_pre_existing:
            return False
        if finding.severity not in self._block_on:
            return False
        return finding.confidence >= self.cfg.minimum_confidence_to_block


class FailureDecision:
    """Deterministic outcome when the LLM is unavailable.

    Unknown on_llm_unavailable values fall back to warn behavior; the config
    layer already restricts the value to block | warn | allow.
    """

    def apply(self, on_llm_unavailable: str, message: str) -> ReviewResult:
        """Map a failure_policy value to a deterministic ReviewResult.

        Never claims the AI review passed — the summary always states the
        commit was reviewed-skipped and which failure_policy outcome applied.
        """
        reason = "AI review was skipped. " + message
        if on_llm_unavailable == "block":
            return ReviewResult(
                decision="BLOCK",
                summary=reason + "\nCommit blocked because failure_policy=block.",
            )
        if on_llm_unavailable == "allow":
            return ReviewResult(
                decision="PASS",
                summary=reason + "\nCommit allowed because failure_policy=allow.",
            )
        return ReviewResult(
            decision="WARN",
            summary=reason + "\nCommit allowed because failure_policy=warn.",
        )
