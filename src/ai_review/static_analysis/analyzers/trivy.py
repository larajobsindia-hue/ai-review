"""Trivy: IaC misconfig scan, never downloads a database in pre-commit."""
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
    "UNKNOWN": "INFO",
}


@registry.register
class TrivyAnalyzer(StaticAnalyzer):
    """``trivy config --skip-db-update`` — mandatory so pre-commit never downloads."""

    name = "trivy"
    executable = "trivy"
    infrastructure = ("Docker", "Terraform", "Kubernetes")
    scope = SCOPE_PROJECT
    ok_exit_codes = (0, 1)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        return [exe, "config", "--format", "json", "--scanners", "misconfig",
                "--skip-db-update", "."]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for payload in load_json_payload(run.stdout):
            results = payload.get("Results")
            if not isinstance(results, list):
                results = [payload] if payload.get("Misconfigurations") else []
            for result in results:
                if not isinstance(result, dict):
                    continue
                target = relative_path(result.get("Target"), ctx.repo_root)
                for item in result.get("Misconfigurations") or []:
                    if not isinstance(item, dict):
                        continue
                    findings.append(self._finding(target, item, ctx))
        return findings

    def _finding(self, target: str, item: dict, ctx: AnalysisContext) -> Finding:
        cause = item.get("CauseMetadata") or {}
        rule_id = item.get("ID") or item.get("AVDID")
        raw = item.get("Severity")
        title = item.get("Title") or item.get("Description") or rule_id or "trivy finding"
        return make_finding(
            tool=self.name,
            severity=normalize_severity(raw, SEVERITY_MAP, "MEDIUM"),
            file=relative_path(cause.get("File") or target, ctx.repo_root),
            line=cause.get("StartLine"),
            message=str(title),
            rule_id=rule_id,
            original_severity=raw,
            evidence=str(item.get("Description") or ""),
        )
