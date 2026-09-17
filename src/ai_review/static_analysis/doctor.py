"""Availability probes for ``--doctor`` (never an error, never a real analysis).

Spec §15/§29: every tool is optional, and a missing one is a warning. Doctor
answers "installed?", not "applicable?": applicability depends on the staged
change set, which doctor does not have.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_review.config import AppConfig
from ai_review.models import RepoProfile
from ai_review.static_analysis import registry
from ai_review.static_analysis.context import AnalysisContext, with_config


@dataclass(frozen=True)
class Probe:
    """One doctor line: a label, an ``ok``/``warn`` status and a note."""
    label: str
    status: Literal["ok", "warn"]
    note: str = ""


def probe_static_analysis(cfg: AppConfig, repo_root: str,
                          profile: RepoProfile) -> list[Probe]:
    """Probe every configured analyzer: enabled / installed / version.

    Never raises and never runs an analysis: a probe either reports the
    tool's version line or degrades to a warning, so doctor stays exit-0 on a
    machine with no analyzers installed at all.
    """
    analysis_cfg = cfg.static_analysis
    if not analysis_cfg.enabled:
        return [Probe(label="Static Analysis", status="warn", note="disabled in config")]
    if not registry.REGISTRY:
        # Nothing registered — SA-1 ships an empty ``MODULES`` list and a
        # broken install looks the same — so report it instead of staying
        # silent. Informational, not a warning: an optional layer with no
        # analyzers must not colour doctor's "System ready." verdict.
        return [Probe(label="Static analysis", status="ok",
                      note="no analyzers registered")]
    ctx = with_config(AnalysisContext(repo_root=repo_root, changes=[], profile=profile),
                      analysis_cfg)
    probes: list[Probe] = []
    for name in sorted(registry.REGISTRY):
        entry = analysis_cfg.tools.get(name)
        if entry is None:
            continue
        analyzer = registry.REGISTRY[name]()
        if not entry.enabled:
            probes.append(Probe(f"Static analysis: {name}", "warn", "disabled in config"))
            continue
        if not analyzer.is_available(ctx):
            probes.append(Probe(f"Static analysis: {name}", "warn", "not installed"))
            continue
        probes.append(Probe(f"Static analysis: {name}", "ok",
                            analyzer.version(ctx) or "available"))
    unconfigured = sorted(set(registry.REGISTRY) - set(analysis_cfg.tools))
    if unconfigured:
        probes.append(Probe("Static analysis tools not configured", "warn",
                            ", ".join(unconfigured)))
    return probes
