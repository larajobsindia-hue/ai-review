"""Checkov: Terraform/Kubernetes/cloud IaC scan."""
from __future__ import annotations

from ai_review.models import Finding
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import SCOPE_PROJECT, AnalysisContext
from ai_review.static_analysis.normalizer import (make_finding, normalize_severity,
                                                  relative_path)
from ai_review.static_analysis.runner import ToolRun, load_json_payload

SEVERITY_MAP = {
    "CRITICAL": "CRITICAL",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
    "INFO": "INFO",
    "UNKNOWN": "INFO",
}


@registry.register
class CheckovAnalyzer(StaticAnalyzer):
    """``checkov -d . -o json --compact`` over detected Terraform/Kubernetes."""

    name = "checkov"
    executable = "checkov"
    infrastructure = ("Terraform", "Kubernetes")
    scope = SCOPE_PROJECT
    ok_exit_codes = (0, 1)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        return [exe, "-d", ".", "-o", "json", "--compact"]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for payload in load_json_payload(run.stdout):
            failed = payload.get("results", {}).get("failed_checks") if isinstance(
                payload.get("results"), dict) else payload.get("failed_checks")
            if not isinstance(failed, list):
                continue
            for item in failed:
                if not isinstance(item, dict):
                    continue
                raw = item.get("severity")
                findings.append(make_finding(
                    tool=self.name,
                    severity=normalize_severity(raw, SEVERITY_MAP, "MEDIUM"),
                    file=relative_path(item.get("file_path"), ctx.repo_root),
                    line=(item.get("file_line_range") or [None])[0],
                    message=str(item.get("check_name") or item.get("check_id")
                                or "checkov finding"),
                    rule_id=item.get("check_id"),
                    original_severity=raw,
                    evidence=str(item.get("guideline") or ""),
                ))
        return findings
