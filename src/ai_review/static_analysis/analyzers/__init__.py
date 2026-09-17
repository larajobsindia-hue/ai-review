"""Shipped analyzers. Each module registers itself in ``registry.REGISTRY``.

The module list is explicit so a missing/broken analyzer module is a hard
import error, never a silently absent tool.
"""
from __future__ import annotations

from importlib import import_module

#: One entry per analyzer module, appended by each phase's tasks.
MODULES: tuple[str, ...] = ("semgrep", "phpstan")


def load_all() -> None:
    """Import every shipped analyzer module (idempotent; imports are cached)."""
    for name in MODULES:
        import_module(f"{__name__}.{name}")
