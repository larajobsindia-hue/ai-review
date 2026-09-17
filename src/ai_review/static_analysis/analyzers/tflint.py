"""TFLint: Terraform linter at project scope."""
from __future__ import annotations

from ai_review.models import Finding
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import SCOPE_PROJECT, AnalysisContext
from ai_review.static_analysis.normalizer import (make_finding, normalize_severity,
                                                  relative_path)
from ai_review.static_analysis.runner import ToolRun, load_json_payload

SEVERITY_MAP = {
    "ERROR": "HIGH",
    "WARNING": "MEDIUM",
    "NOTICE": "LOW",
}


@registry.register
class TflintAnalyzer(StaticAnalyzer):
    """``tflint --format json`` when Terraform is detected."""

    name = "tflint"
    executable = "tflint"
    infrastructure = ("Terraform",)
    scope = SCOPE_PROJECT
    ok_exit_codes = (0, 1, 2)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        return [exe, "--format", "json"]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for payload in load_json_payload(run.stdout):
            issues = payload.get("issues")
            if not isinstance(issues, list):
                continue
            for item in issues:
                if not isinstance(item, dict):
                    continue
                rng = item.get("range") or {}
                raw = item.get("severity")
                findings.append(make_finding(
                    tool=self.name,
                    severity=normalize_severity(raw, SEVERITY_MAP, "MEDIUM"),
                    file=relative_path(rng.get("filename"), ctx.repo_root),
                    line=(rng.get("start") or {}).get("line"),
                    message=str(item.get("message") or item.get("rule")
                                or "tflint finding"),
                    rule_id=(item.get("rule") or {}).get("name")
                    if isinstance(item.get("rule"), dict) else item.get("rule"),
                    original_severity=raw,
                ))
        return findings
