"""Semgrep: pattern-based findings, handed to the AI as evidence only.

Never ``--config auto``. A pre-commit must make no network calls of its own
(spec §12/§16), and ``auto`` fetches its rule pack on first use — so semgrep
runs only when the repository ships a rule config, and telemetry/version checks
are disabled both by flag and by environment.
"""
from __future__ import annotations

import os

from ai_review.models import Finding, Severity
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import AnalysisContext
from ai_review.static_analysis.normalizer import (confidence_from, make_finding,
                                                  normalize_severity, relative_path)
from ai_review.static_analysis.runner import ToolRun, load_json_payload

#: Semgrep's own severities; ``INFO`` is the only one that is purely advisory.
SEVERITY_MAP: dict[str, Severity] = {
    "ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW",
}
DEFAULT_SEVERITY: Severity = "MEDIUM"

#: Rule config the repository may ship (semgrep's own documented locations).
CONFIG_CANDIDATES: tuple[str, ...] = (
    "semgrep.yml", "semgrep.yaml", ".semgrep.yml", ".semgrep.yaml",
)
#: Directory scanned for per-rule-pack configs, in sorted order.
CONFIG_DIR = ".semgrep"


@registry.register
class SemgrepAnalyzer(StaticAnalyzer):
    """``semgrep scan --json`` over the changed files of the detected languages."""

    name = "semgrep"
    executable = "semgrep"
    extensions = (".php", ".py", ".js", ".ts", ".go", ".rb", ".java", ".sql")
    #: semgrep exits 1 when it *found* something — that is a successful run.
    ok_exit_codes = (0, 1)
    env = {"SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0"}

    def rule_config(self, ctx: AnalysisContext) -> str | None:
        """The repository's rule config, or ``None`` (never ``--config auto``)."""
        for candidate in CONFIG_CANDIDATES:
            if ctx.has_file(candidate):
                return candidate
        directory = os.path.join(ctx.repo_root, CONFIG_DIR)
        if os.path.isdir(directory):
            for name in sorted(os.listdir(directory)):
                if name.lower().endswith((".yml", ".yaml")):
                    return os.path.join(CONFIG_DIR, name)
        return None

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        config = self.rule_config(ctx)
        targets = ctx.changed_paths(self.extensions)
        if config is None or not targets:
            return []                      # reporting "no targets" beats a scan of nothing
        return [exe, "scan", "--json", "--quiet", "--disable-version-check",
                "--metrics=off", "--config", config, *targets]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for payload in load_json_payload(run.stdout):
            results = payload.get("results")
            if not isinstance(results, list):
                results = [payload] if payload.get("check_id") else []
            for item in results:
                if not isinstance(item, dict):
                    continue
                findings.append(self._finding(item, ctx))
        return findings

    def _finding(self, item: dict, ctx: AnalysisContext) -> Finding:
        extra = item.get("extra") or {}
        metadata = extra.get("metadata") or {}
        rule_id = item.get("check_id")
        message = extra.get("message") or rule_id or "semgrep finding"
        cwe = metadata.get("cwe") or []
        severity = normalize_severity(extra.get("severity"), SEVERITY_MAP,
                                      DEFAULT_SEVERITY)
        is_security = str(metadata.get("category") or "").strip().lower() == "security"
        start = item.get("start") or {}
        return make_finding(
            tool=self.name,
            severity=severity,
            file=relative_path(item.get("path"), ctx.repo_root),
            line=start.get("line"),
            message=str(message),
            rule_id=rule_id,
            # A rule whose metadata says "security" is security even when its id
            # does not: the tool's own taxonomy outranks keyword inference.
            category="SECURITY" if is_security else None,
            confidence=confidence_from(metadata.get("confidence")),
            evidence="; ".join(str(entry) for entry in cwe if entry),
            fingerprint=extra.get("fingerprint"),
            original_severity=extra.get("severity"),
        )
