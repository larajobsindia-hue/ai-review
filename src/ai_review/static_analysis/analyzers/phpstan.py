"""PHPStan: PHP type/flow analysis, run at project scope.

PHPStan cannot analyse an isolated file — the same class resolved differently in
two configurations is the whole point of the tool — so it declares
``scope = project`` and lets its own config decide the paths. Without a config
it is pointed at the changed PHP files, which is approximate by definition and
still better than nothing.
"""
from __future__ import annotations

from ai_review.models import Finding, Severity
from ai_review.static_analysis import registry
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import SCOPE_PROJECT, AnalysisContext
from ai_review.static_analysis.normalizer import make_finding, relative_path
from ai_review.static_analysis.runner import ToolRun, load_json_payload

#: PHPStan exits 1 when it found errors: a successful run.
OK_EXIT_CODES = (0, 1)

#: Identifier prefixes -> severity, first match wins. PHPStan has no severity
#: levels of its own, so this table *is* the severity policy; anything unlisted
#: is MEDIUM (a real report that is neither a crash nor a style note).
RULE_SEVERITIES: tuple[tuple[str, Severity], ...] = (
    # Wrong types, undefined symbols, impossible operations: the bugs that
    # survive to production and crash (or silently misbehave) at runtime.
    ("argument.", "HIGH"),
    ("method.nonObject", "HIGH"),
    ("property.nonObject", "HIGH"),
    ("variable.undefined", "HIGH"),
    ("class.notFound", "HIGH"),
    ("function.notFound", "HIGH"),
    ("method.notFound", "HIGH"),
    ("return.type", "HIGH"),
    ("binaryOp.invalid", "HIGH"),
    ("offsetAccess.nonOffsetAccessible", "HIGH"),
    # Missing annotations, dead code, unused and deprecated API: maintainability.
    ("missingType.", "LOW"),
    ("missingReturn.", "LOW"),
    ("deadCode.", "LOW"),
    ("unused", "LOW"),
    ("deprecated", "LOW"),
)

DEFAULT_SEVERITY: Severity = "MEDIUM"

#: A repository config wins: it already names the paths and the level to report.
CONFIG_CANDIDATES: tuple[str, ...] = (
    "phpstan.neon", "phpstan.neon.dist", "phpstan.dist.neon",
)


def severity_for(identifier: str | None) -> Severity:
    """Map a PHPStan error identifier to a normalized severity."""
    text = (identifier or "").strip()
    if not text:
        return DEFAULT_SEVERITY
    for prefix, severity in RULE_SEVERITIES:
        if text == prefix or text.startswith(prefix):
            return severity
    return DEFAULT_SEVERITY


@registry.register
class PhpstanAnalyzer(StaticAnalyzer):
    """``phpstan analyse --error-format=json`` (project scope)."""

    name = "phpstan"
    executable = "phpstan"
    languages = ("PHP",)
    scope = SCOPE_PROJECT
    #: A composer-installed PHPStan beats whatever happens to be on PATH.
    local_paths = ("vendor/bin/phpstan", "vendor/bin/phpstan.phar")
    ok_exit_codes = OK_EXIT_CODES

    def has_project_config(self, ctx: AnalysisContext) -> bool:
        return any(ctx.has_file(candidate) for candidate in CONFIG_CANDIDATES)

    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        argv = [exe, "analyse", "--error-format=json", "--no-progress",
                "--no-interaction"]
        if self.has_project_config(ctx):
            return argv                    # the config owns the analysed paths
        targets = ctx.changed_paths((".php",))
        if not targets:
            return []                      # nothing to analyse, never a whole-repo scan
        return [*argv, *targets]

    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        findings: list[Finding] = []
        for payload in load_json_payload(run.stdout):
            files = payload.get("files")
            if not isinstance(files, dict):
                continue
            for path in sorted(files):     # sorted: stable order across runs
                entry = files[path] or {}
                for message in entry.get("messages") or []:
                    if isinstance(message, dict):
                        findings.append(self._finding(path, message, ctx))
        return findings

    def _finding(self, path: str, message: dict, ctx: AnalysisContext) -> Finding:
        identifier = message.get("identifier")
        text = str(message.get("message") or identifier or "phpstan finding")
        return make_finding(
            tool=self.name,
            severity=severity_for(identifier),
            file=relative_path(path, ctx.repo_root),
            line=message.get("line"),
            message=text,
            rule_id=identifier,
            # No original_severity: PHPStan has no severity vocabulary, and
            # inventing one would misrepresent the tool (spec §19).
            evidence=f"identifier: {identifier}" if identifier else "",
        )
