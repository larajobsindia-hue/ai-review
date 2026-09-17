"""SonarQube: CI-only. Requires an explicit server; never a pre-commit network call."""
from __future__ import annotations

from ai_review.models import AnalyzerResult, Finding
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import SCOPE_PROJECT, AnalysisContext
from ai_review.static_analysis.runner import ToolRun


@registry.register
class SonarqubeAnalyzer(StaticAnalyzer):
    """Placeholder until server URL + token are configurable.

    Reports skipped/unavailable with a clear reason and never contacts a
    server from the pre-commit path (plan SA-4). Default ``enabled: false``.
    """

    name = "sonarqube"
    executable = "sonar-scanner"
    scope = SCOPE_PROJECT
    ok_exit_codes = (0,)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        return []

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        if self.resolve(ctx) is None:
            return AnalyzerResult(status="unavailable",
                                  error="sonar-scanner not found")
        return AnalyzerResult(
            status="skipped",
            error="sonarqube requires an explicit server URL and token (CI-only)",
        )

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        return []
