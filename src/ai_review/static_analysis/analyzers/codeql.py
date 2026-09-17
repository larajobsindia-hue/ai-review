"""CodeQL: CI-only. Never downloads a database from a pre-commit."""
from __future__ import annotations

from ai_review.models import AnalyzerResult, Finding
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import SCOPE_PROJECT, AnalysisContext
from ai_review.static_analysis.runner import ToolRun


@registry.register
class CodeqlAnalyzer(StaticAnalyzer):
    """Placeholder until per-tool ``args``/database config exists.

    Reports skipped with a clear reason and never downloads a database
    (plan SA-4). Default ``enabled: false``.
    """

    name = "codeql"
    executable = "codeql"
    scope = SCOPE_PROJECT
    ok_exit_codes = (0,)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        return []

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        if self.resolve(ctx) is None:
            return AnalyzerResult(status="unavailable",
                                  error="codeql not found")
        return AnalyzerResult(
            status="skipped",
            error="codeql requires an explicit database path (CI-only; never downloads)",
        )

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        return []
