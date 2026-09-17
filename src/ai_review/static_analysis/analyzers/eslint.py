"""ESLint: JS/TS lint findings as evidence for the AI reviewer."""
from __future__ import annotations

from ai_review.models import Category, Finding, Severity
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import AnalysisContext
from ai_review.static_analysis.normalizer import make_finding, relative_path
from ai_review.static_analysis.runner import ToolRun, load_json_payload


def _severity_and_category(rule_id: str | None,
                           raw: object) -> tuple[Severity, Category | None, str | None]:
    """Map one ESLint message to normalized severity + optional category."""
    original = str(raw) if raw is not None and raw != "" else None
    is_security = "security" in str(rule_id or "").lower()
    if is_security:
        return "HIGH", "SECURITY", original
    if raw == 2 or original == "2":
        return "MEDIUM", None, original
    return "LOW", None, original


@registry.register
class EslintAnalyzer(StaticAnalyzer):
    """``eslint --format json`` over staged JS/TS files."""

    name = "eslint"
    executable = "eslint"
    languages = ("JavaScript", "TypeScript")
    extensions = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
    local_paths = ("node_modules/.bin/eslint",)
    ok_exit_codes = (0, 1)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        targets = ctx.changed_paths(self.extensions)
        if not targets:
            return []
        return [exe, "--format", "json", "--no-color",
                "--no-error-on-unmatched-pattern", *targets]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for payload in load_json_payload(run.stdout):
            path = relative_path(payload.get("filePath"), ctx.repo_root)
            for message in payload.get("messages") or []:
                if isinstance(message, dict):
                    findings.append(self._finding(path, message, ctx))
        return findings

    def _finding(self, path: str, message: dict, ctx: AnalysisContext) -> Finding:
        rule_id = message.get("ruleId")
        text = str(message.get("message") or rule_id or "eslint finding")
        severity, category, original = _severity_and_category(rule_id, message.get("severity"))
        return make_finding(
            tool=self.name,
            severity=severity,
            file=path or relative_path(message.get("filePath"), ctx.repo_root),
            line=message.get("line"),
            message=text,
            rule_id=rule_id,
            category=category,
            original_severity=original,
        )
