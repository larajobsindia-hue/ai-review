"""kube-linter: Kubernetes best-practice lint at project scope."""
from __future__ import annotations

from ai_review.models import Finding
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import SCOPE_PROJECT, AnalysisContext
from ai_review.static_analysis.normalizer import make_finding, relative_path
from ai_review.static_analysis.runner import ToolRun, load_json_payload

_MANIFEST_EXTS = (".yaml", ".yml", ".json")


@registry.register
class KubeLinterAnalyzer(StaticAnalyzer):
    """``kube-linter lint --format json`` over staged Kubernetes manifests."""

    name = "kube_linter"
    executable = "kube-linter"
    infrastructure = ("Kubernetes",)
    scope = SCOPE_PROJECT
    ok_exit_codes = (0, 1)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        targets = ctx.changed_paths(_MANIFEST_EXTS)
        if not targets:
            return []
        return [exe, "lint", "--format", "json", *targets]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for payload in load_json_payload(run.stdout):
            reports = payload.get("Reports")
            if not isinstance(reports, list):
                continue
            for item in reports:
                if not isinstance(item, dict):
                    continue
                diag = item.get("Diagnostic") or {}
                obj = item.get("Object") or {}
                metadata = obj.get("Metadata") or {}
                file_path = metadata.get("FilePath") or obj.get("FilePath")
                findings.append(make_finding(
                    tool=self.name,
                    severity="MEDIUM",
                    file=relative_path(file_path, ctx.repo_root),
                    line=None,
                    message=str(diag.get("Message") or item.get("Check")
                                or "kube-linter finding"),
                    rule_id=item.get("Check"),
                ))
        return findings
