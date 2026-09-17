"""Everything an analyzer may inspect, and the targets it may be pointed at.

The context is deliberately read-only with respect to the review: analyzers
read the working tree, never the staged diff blob, so a git-only view can't
hide what the tools must see (same reasoning as ``resolution.py``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from ai_review.config import AppConfig, StaticAnalysisConfig
from ai_review.models import RepoProfile, StagedChange

#: Scope of an analyzer's file arguments (spec §17).
SCOPE_CHANGED_FILES = "changed_files"
SCOPE_PROJECT = "project"


@dataclass
class AnalysisContext:
    """Immutable-by-convention view passed to every analyzer."""
    repo_root: str
    changes: list[StagedChange] = field(default_factory=list)
    profile: RepoProfile = field(default_factory=RepoProfile)
    diff_text: str = ""
    #: Set by ``build_context`` / ``with_config``; never None inside analyze().
    cfg: StaticAnalysisConfig | None = None

    def languages(self) -> set[str]:
        return {entry.name for entry in self.profile.languages}

    def frameworks(self) -> set[str]:
        return {entry.name for entry in self.profile.frameworks}

    def changed_paths(self, extensions: tuple[str, ...] = ()) -> list[str]:
        """Repo-relative staged paths that exist on disk, optionally by extension.

        Deleted and binary changes are excluded (there is no readable file, and
        a tool pointed at a missing path is an error, not evidence), as are
        paths whose on-disk file is gone.
        """
        out: list[str] = []
        for change in self.changes:
            if change.status == "deleted" or change.is_binary:
                continue
            if extensions and not change.path.lower().endswith(extensions):
                continue
            if os.path.isfile(os.path.join(self.repo_root, change.path)):
                out.append(change.path)
        return out

    def has_file(self, rel: str) -> bool:
        """True when a repo-relative path exists (manifest/config discovery)."""
        return os.path.isfile(os.path.join(self.repo_root, rel))

    def has_any(self, rels: tuple[str, ...]) -> bool:
        return any(self.has_file(rel) for rel in rels)


def build_context(repo_root: str, cfg: AppConfig, changes: list[StagedChange],
                  profile: RepoProfile, diff_text: str = "") -> AnalysisContext:
    """Build the context from the pipeline's already-computed offline state."""
    return AnalysisContext(
        repo_root=repo_root, changes=list(changes), profile=profile,
        diff_text=diff_text, cfg=cfg.static_analysis,
    )


def with_config(ctx: AnalysisContext, cfg: StaticAnalysisConfig) -> AnalysisContext:
    """Return *ctx* with *cfg* attached (keeps ``run_static_analysis(cfg, ctx)``
    honest about which config the analyzers actually see)."""
    return replace(ctx, cfg=cfg)
