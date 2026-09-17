"""Ruff: lightweight Python linter, primary analyzer when available."""
from __future__ import annotations

from ai_review.models import Category, Finding, Severity
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import AnalysisContext
from ai_review.static_analysis.normalizer import make_finding, relative_path
from ai_review.static_analysis.runner import ToolRun, load_json_payload


def _severity_and_category(code: str | None) -> tuple[Severity, Category | None]:
    """Map a Ruff rule code. Unknown codes fall back to LOW (plan Task 20)."""
    text = str(code or "")
    if text.startswith("S"):
        return "HIGH", "SECURITY"
    if text == "F821":
        return "HIGH", "BUG"
    return "LOW", None


@registry.register
class RuffAnalyzer(StaticAnalyzer):
    """``ruff check --output-format json`` over staged Python files."""

    name = "ruff"
    executable = "ruff"
    languages = ("Python",)
    extensions = (".py", ".pyi")
    ok_exit_codes = (0, 1)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        targets = ctx.changed_paths(self.extensions)
        if not targets:
            return []
        return [exe, "check", "--output-format", "json", "--no-cache", "--quiet",
                *targets]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for item in load_json_payload(run.stdout):
            code = item.get("code")
            location = item.get("location") or {}
            severity, category = _severity_and_category(code)
            text = str(item.get("message") or code or "ruff finding")
            findings.append(make_finding(
                tool=self.name,
                severity=severity,
                file=relative_path(item.get("filename"), ctx.repo_root),
                line=location.get("row"),
                message=text,
                rule_id=code,
                category=category,
                evidence=str(item.get("url") or ""),
            ))
        return findings
