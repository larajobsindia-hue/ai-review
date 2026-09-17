"""kubeconform: Kubernetes manifest schema validation."""
from __future__ import annotations

from ai_review.models import Finding
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import AnalysisContext
from ai_review.static_analysis.normalizer import make_finding, relative_path
from ai_review.static_analysis.runner import ToolRun, load_json_payload

_MANIFEST_EXTS = (".yaml", ".yml", ".json")


def _manifest_targets(ctx: AnalysisContext) -> list[str]:
    return ctx.changed_paths(_MANIFEST_EXTS)


@registry.register
class KubeconformAnalyzer(StaticAnalyzer):
    """``kubeconform -output json`` over staged Kubernetes manifests."""

    name = "kubeconform"
    executable = "kubeconform"
    infrastructure = ("Kubernetes",)
    extensions = _MANIFEST_EXTS
    ok_exit_codes = (0, 1)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        targets = _manifest_targets(ctx)
        if not targets:
            return []
        return [exe, "-output", "json", *targets]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for payload in load_json_payload(run.stdout):
            resources = payload.get("resources")
            items = resources if isinstance(resources, list) else [payload]
            for item in items:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status") or "").lower()
                if status in ("", "ok", "skipped", "valid"):
                    continue
                msg = item.get("msg") or item.get("reason") or status
                findings.append(make_finding(
                    tool=self.name,
                    severity="HIGH",
                    file=relative_path(item.get("filename"), ctx.repo_root),
                    line=None,
                    message=str(msg),
                    original_severity=status,
                ))
        return findings
