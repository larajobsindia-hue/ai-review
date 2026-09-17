"""Analyzer contract: identity + gating + argv + parser, nothing else.

``analyze`` is a template method so every analyzer inherits the same safety
properties (list argv, pinned cwd, bounded timeout, fault isolation) and a new
tool is one small module that only declares *what* to run and how to read it.
"""
from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod

from ai_review.config import StaticAnalysisConfig
from ai_review.models import AnalyzerResult, Finding
from ai_review.static_analysis.context import SCOPE_CHANGED_FILES, AnalysisContext
from ai_review.static_analysis.runner import ToolRun, run_tool

#: Availability/version probes are cheap and separate from a real analysis.
VERSION_TIMEOUT_S = 5.0
#: How much tool stderr is echoed into a failure message.
ERROR_TAIL_CHARS = 500

_VERSION_CACHE: dict[tuple[str, str], str] = {}


class StaticAnalysisError(RuntimeError):
    """Raised only when ``static_analysis.fail_on_error: true`` and a tool failed."""


def tool_enabled(cfg: StaticAnalysisConfig | None, name: str) -> bool:
    """Explicit per-tool opt-in; a missing config enables nothing."""
    if cfg is None:
        return False
    entry = cfg.tools.get(name)
    return bool(entry and entry.enabled)


class StaticAnalyzer(ABC):
    """One external static-analysis tool."""

    name: str = ""
    #: PATH executable name.
    executable: str = ""
    #: Repo-relative candidates tried before PATH (phpstan lives in vendor/bin).
    local_paths: tuple[str, ...] = ()
    #: Technology gates; empty means "any technology".
    languages: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    #: Infrastructure gates (Docker, Terraform, Kubernetes); empty means "any".
    infrastructure: tuple[str, ...] = ()
    #: File extensions consumed in changed_files scope; empty means "any".
    extensions: tuple[str, ...] = ()
    scope: str = SCOPE_CHANGED_FILES
    version_args: tuple[str, ...] = ("--version",)
    #: Exit codes that mean "ran fine" — linters exit non-zero when they report.
    ok_exit_codes: tuple[int, ...] = (0,)
    #: Extra environment forced for this tool (e.g. metrics off).
    env: dict[str, str] = {}

    # -- gating ------------------------------------------------------------

    def supports(self, ctx: AnalysisContext) -> bool:
        """Enabled by config *and* relevant to the detected technology/targets."""
        if not tool_enabled(ctx.cfg, self.name):
            return False
        if self.languages and not ctx.languages().intersection(self.languages):
            return False
        if self.frameworks and not ctx.frameworks().intersection(self.frameworks):
            return False
        if self.infrastructure and not ctx.infrastructure().intersection(self.infrastructure):
            return False
        if self.scope == SCOPE_CHANGED_FILES and self.extensions and not \
                ctx.changed_paths(self.extensions):
            return False
        return True

    def resolve(self, ctx: AnalysisContext) -> str | None:
        """Absolute executable path: project-local candidates first, then PATH."""
        for rel in self.local_paths:
            candidate = os.path.join(ctx.repo_root, rel)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return shutil.which(self.executable) if self.executable else None

    def is_available(self, ctx: AnalysisContext) -> bool:
        return self.resolve(ctx) is not None

    def version(self, ctx: AnalysisContext) -> str:
        """Best-effort first line of ``--version`` (5s, cached per repo)."""
        key = (self.name, ctx.repo_root)
        if key in _VERSION_CACHE:
            return _VERSION_CACHE[key]
        text = ""
        exe = self.resolve(ctx)
        if exe:
            run = run_tool([exe, *self.version_args], cwd=ctx.repo_root,
                           timeout=VERSION_TIMEOUT_S, env=self.env)
            lines = [line.strip() for line in (run.stdout or run.stderr).splitlines()
                     if line.strip()]
            text = lines[0][:120] if lines else ""
        _VERSION_CACHE[key] = text
        return text

    # -- execution ---------------------------------------------------------

    def analyze(self, ctx: AnalysisContext) -> AnalyzerResult:
        """Resolve, run, parse. Never raises for tool behaviour."""
        exe = self.resolve(ctx)
        if exe is None:
            return AnalyzerResult(status="unavailable",
                                  error=f"{self.executable or self.name} not found")
        argv = self.build_argv(exe, ctx)
        if not argv:
            return AnalyzerResult(status="skipped", error="no targets")
        timeout = ctx.cfg.timeout if ctx.cfg else 120
        run = run_tool(argv, cwd=ctx.repo_root, timeout=timeout, env=self.env)
        if run.status != "run":
            return AnalyzerResult(status="failed", exit_code=run.exit_code,
                                  error=run.error or run.status,
                                  duration_ms=run.duration_ms)
        if run.exit_code not in self.ok_exit_codes:
            detail = (run.stderr or run.stdout).strip()[-ERROR_TAIL_CHARS:]
            return AnalyzerResult(status="failed", exit_code=run.exit_code,
                                  error=detail or f"exit code {run.exit_code}",
                                  duration_ms=run.duration_ms)
        try:
            findings = self.parse(run, ctx)
        except Exception as exc:                       # analyzer/parser bug
            return AnalyzerResult(status="failed", exit_code=run.exit_code,
                                  error=f"parse error: {exc}", duration_ms=run.duration_ms)
        return AnalyzerResult(findings=list(findings), status="run",
                              exit_code=run.exit_code, duration_ms=run.duration_ms)

    @abstractmethod
    def build_argv(self, exe: str, ctx: AnalysisContext) -> list[str]:
        """Explicit argv list (never a shell string). Empty == nothing to do."""

    @abstractmethod
    def parse(self, run: ToolRun, ctx: AnalysisContext) -> list[Finding]:
        """Normalize tool output into static findings."""
