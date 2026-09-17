"""Hadolint: Dockerfile lint, Dockerfile paths only."""
from __future__ import annotations

import os

from ai_review.models import Finding
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import AnalysisContext
from ai_review.static_analysis.normalizer import (make_finding, normalize_severity,
                                                  relative_path)
from ai_review.static_analysis.runner import ToolRun, load_json_payload

SEVERITY_MAP = {
    "ERROR": "HIGH",
    "WARNING": "MEDIUM",
    "INFO": "LOW",
    "STYLE": "LOW",
}


def dockerfile_targets(ctx: AnalysisContext) -> list[str]:
    """Staged files whose basename is ``Dockerfile``, ``Dockerfile.*``, or ``*.dockerfile``."""
    out: list[str] = []
    for path in ctx.changed_paths():
        base = os.path.basename(path)
        lower = base.lower()
        if base == "Dockerfile" or base.startswith("Dockerfile.") or lower.endswith(".dockerfile"):
            out.append(path)
    return out


@registry.register
class HadolintAnalyzer(StaticAnalyzer):
    """``hadolint --format json`` over staged Dockerfiles."""

    name = "hadolint"
    executable = "hadolint"
    infrastructure = ("Docker",)
    ok_exit_codes = (0, 1)

    def supports(self, ctx: AnalysisContext) -> bool:
        if not super().supports(ctx):
            return False
        return bool(dockerfile_targets(ctx))

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        targets = dockerfile_targets(ctx)
        if not targets:
            return []
        return [exe, "--format", "json", *targets]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for item in load_json_payload(run.stdout):
            level = item.get("level")
            findings.append(make_finding(
                tool=self.name,
                severity=normalize_severity(level, SEVERITY_MAP, "MEDIUM"),
                file=relative_path(item.get("file"), ctx.repo_root),
                line=item.get("line"),
                message=str(item.get("message") or item.get("code") or "hadolint finding"),
                rule_id=item.get("code"),
                original_severity=level,
            ))
        return findings
