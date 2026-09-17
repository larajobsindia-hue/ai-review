"""staticcheck: Go static analysis at project scope."""
from __future__ import annotations

from ai_review.models import Finding
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import SCOPE_PROJECT, AnalysisContext
from ai_review.static_analysis.normalizer import (make_finding, normalize_severity,
                                                  relative_path)
from ai_review.static_analysis.runner import ToolRun, load_json_payload

SEVERITY_MAP = {"ERROR": "HIGH", "WARNING": "MEDIUM"}


@registry.register
class StaticcheckAnalyzer(StaticAnalyzer):
    """``staticcheck -f json ./...`` when the repository has a ``go.mod``."""

    name = "staticcheck"
    executable = "staticcheck"
    languages = ("Go",)
    scope = SCOPE_PROJECT
    ok_exit_codes = (0, 1)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        if not ctx.has_file("go.mod"):
            return []
        return [exe, "-f", "json", "./..."]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for item in load_json_payload(run.stdout):
            loc = item.get("location") or {}
            code = item.get("code")
            raw = item.get("severity")
            category = "MAINTAINABILITY" if str(code or "").startswith("ST1") else None
            findings.append(make_finding(
                tool=self.name,
                severity=normalize_severity(raw, SEVERITY_MAP, "MEDIUM"),
                file=relative_path(loc.get("file"), ctx.repo_root),
                line=loc.get("line"),
                message=str(item.get("message") or code or "staticcheck finding"),
                rule_id=code,
                category=category,
                original_severity=raw,
            ))
        return findings
