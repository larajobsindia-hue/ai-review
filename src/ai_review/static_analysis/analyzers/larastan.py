"""Larastan: PHPStan for Laravel, only when Laravel is detected."""
from __future__ import annotations

from ai_review.models import Finding
from ai_review.static_analysis import registry
from ai_review.static_analysis.analyzers.phpstan import parse_phpstan_report
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import SCOPE_PROJECT, AnalysisContext
from ai_review.static_analysis.runner import ToolRun


@registry.register
class LarastanAnalyzer(StaticAnalyzer):
    """``larastan analyse --error-format=json`` (Laravel projects only)."""

    name = "larastan"
    executable = "larastan"
    languages = ("PHP",)
    frameworks = ("Laravel",)
    scope = SCOPE_PROJECT
    local_paths = ("vendor/bin/larastan",)
    ok_exit_codes = (0, 1)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        return [exe, "analyse", "--error-format=json", "--no-progress"]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        return parse_phpstan_report(run.stdout, ctx, tool=self.name)
