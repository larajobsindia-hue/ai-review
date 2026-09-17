"""Analyzer registry and config-driven selection.

The loader call must stay at the *bottom* of this module: analyzer modules
import ``registry`` to call ``register``, so ``REGISTRY`` and ``register`` must
already exist by the time the first one is imported.
"""
from __future__ import annotations

from ai_review.config import StaticAnalysisConfig
from ai_review.models import ToolResult
from ai_review.static_analysis.base import StaticAnalyzer
from ai_review.static_analysis.context import AnalysisContext

#: name -> analyzer class. Tests inject fakes with ``monkeypatch.setitem``.
REGISTRY: dict[str, type[StaticAnalyzer]] = {}

#: ``ToolResult.error`` for an enabled analyzer that does not match this change
#: set. Shared so the dry-run plan can tell "would run" from "cannot apply"
#: without matching on prose.
NOT_APPLICABLE = "not applicable to this change set"


def register(cls: type[StaticAnalyzer], *,
             replace: bool = False) -> type[StaticAnalyzer]:
    """Register an analyzer class under its ``name``."""
    if not cls.name:
        raise ValueError("analyzer must declare a name")
    if cls.name in REGISTRY and not replace:
        raise ValueError(f"analyzer {cls.name!r} already registered")
    REGISTRY[cls.name] = cls
    return cls


def build_candidates(cfg: StaticAnalysisConfig,
                     ctx: AnalysisContext) -> tuple[list[StaticAnalyzer], list[ToolResult]]:
    """Split configured analyzers into *runnable* and *skipped* (not applicable).

    Analyzers that are not enabled in config are absent entirely (they were
    never asked for); enabled ones that do not match the detected technology or
    the changed files are reported as ``skipped`` so the run's summary stays
    honest about what was considered.
    """
    runnable: list[StaticAnalyzer] = []
    skipped: list[ToolResult] = []
    for name in sorted(REGISTRY):
        entry = cfg.tools.get(name)
        if entry is None or not entry.enabled:
            continue
        analyzer = REGISTRY[name]()
        if analyzer.supports(ctx):
            runnable.append(analyzer)
        else:
            skipped.append(ToolResult(name=name, status="skipped",
                                      error=NOT_APPLICABLE))
    return runnable, skipped


def load_builtin() -> None:
    """Import the shipped analyzer modules once (idempotent)."""
    from ai_review.static_analysis.analyzers import load_all
    load_all()


load_builtin()
