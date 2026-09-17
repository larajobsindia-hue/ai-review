"""SQLFluff: lint staged ``.sql`` files only."""
from __future__ import annotations

from ai_review.models import Finding, Severity
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import AnalysisContext
from ai_review.static_analysis.normalizer import make_finding, relative_path
from ai_review.static_analysis.runner import ToolRun, load_json_payload


def _severity_for(code: str | None) -> Severity:
    """PRS/L → MEDIUM, LT/CP → LOW, anything else MEDIUM."""
    text = str(code or "")
    if text.startswith(("LT", "CP")):
        return "LOW"
    return "MEDIUM"


@registry.register
class SqlfluffAnalyzer(StaticAnalyzer):
    """``sqlfluff lint --format json`` over staged SQL files."""

    name = "sqlfluff"
    executable = "sqlfluff"
    languages = ("SQL",)
    extensions = (".sql",)
    ok_exit_codes = (0, 1)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        targets = ctx.changed_paths(self.extensions)
        if not targets:
            return []
        return [exe, "lint", "--format", "json", *targets]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for payload in load_json_payload(run.stdout):
            path = relative_path(payload.get("filepath"), ctx.repo_root)
            for violation in payload.get("violations") or []:
                if not isinstance(violation, dict):
                    continue
                code = violation.get("code")
                findings.append(make_finding(
                    tool=self.name,
                    severity=_severity_for(code),
                    file=path,
                    line=violation.get("line_no"),
                    message=str(violation.get("description") or code or "sqlfluff finding"),
                    rule_id=code,
                ))
        return findings
