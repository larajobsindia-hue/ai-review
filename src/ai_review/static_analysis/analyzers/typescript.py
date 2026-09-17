"""TypeScript compiler: type-check only, never execute application code."""
from __future__ import annotations

import re

from ai_review.models import Finding, Severity
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import SCOPE_PROJECT, AnalysisContext
from ai_review.static_analysis.normalizer import make_finding, relative_path
from ai_review.static_analysis.runner import ToolRun

#: ``file(line,col): error TSxxxx: message`` (pretty=false).
_TS_LINE = re.compile(
    r"^(?P<file>.+)\((?P<line>\d+),(?P<col>\d+)\): "
    r"(?P<level>error|warning) (?P<code>TS\d+): (?P<msg>.*)$"
)

CONFIG_CANDIDATES: tuple[str, ...] = ("tsconfig.json", "tsconfig.app.json")


def _severity_for(code: str) -> Severity:
    """TS1xxx syntax and TS2xxx type errors are HIGH; TS6xxx config notes are LOW."""
    if code.startswith(("TS1", "TS2")):
        return "HIGH"
    if code.startswith("TS6"):
        return "LOW"
    return "MEDIUM"


@registry.register
class TypescriptAnalyzer(StaticAnalyzer):
    """``tsc --noEmit --pretty false`` (respects tsconfig.json)."""

    name = "typescript"
    executable = "tsc"
    languages = ("TypeScript",)
    scope = SCOPE_PROJECT
    local_paths = ("node_modules/.bin/tsc",)
    #: tsc exits 2 on type errors.
    ok_exit_codes = (0, 1, 2)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        if not any(ctx.has_file(candidate) for candidate in CONFIG_CANDIDATES):
            return []
        return [exe, "--noEmit", "--pretty", "false"]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        text = "\n".join((run.stdout or "", run.stderr or ""))
        for raw_line in text.splitlines():
            match = _TS_LINE.match(raw_line.strip())
            if not match:
                continue
            code = match.group("code")
            findings.append(make_finding(
                tool=self.name,
                severity=_severity_for(code),
                file=relative_path(match.group("file"), ctx.repo_root),
                line=int(match.group("line")),
                message=match.group("msg").strip(),
                rule_id=code,
                original_severity=match.group("level"),
            ))
        return findings
