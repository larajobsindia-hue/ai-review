"""go vet: compiler-adjacent checks at project scope."""
from __future__ import annotations

import re

from ai_review.models import Finding
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import SCOPE_PROJECT, AnalysisContext
from ai_review.static_analysis.normalizer import make_finding, relative_path
from ai_review.static_analysis.runner import ToolRun

_GOVET_LINE = re.compile(
    r"^(?:vet: )?(?P<file>.+\.go):(?P<line>\d+)(?::(?P<col>\d+))?: (?P<msg>.+)$"
)


@registry.register
class GovetAnalyzer(StaticAnalyzer):
    """``go vet ./...`` when the repository has a ``go.mod``."""

    name = "govet"
    executable = "go"
    version_args = ("version",)
    languages = ("Go",)
    scope = SCOPE_PROJECT
    ok_exit_codes = (0, 1)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        if not ctx.has_file("go.mod"):
            return []
        return [exe, "vet", "./..."]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        text = "\n".join((run.stdout or "", run.stderr or ""))
        for raw_line in text.splitlines():
            match = _GOVET_LINE.match(raw_line.strip())
            if not match:
                continue
            findings.append(make_finding(
                tool=self.name,
                severity="MEDIUM",
                file=relative_path(match.group("file"), ctx.repo_root),
                line=int(match.group("line")),
                message=match.group("msg").strip(),
            ))
        return findings
