"""Classify changed files to decide review depth."""
from __future__ import annotations

import fnmatch
import os

from ai_review.models import ChangeKind, FileClass

BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".tar", ".woff", ".woff2", ".ttf", ".mp4", ".class", ".jar", ".pyc",
    ".so", ".dll", ".exe",
})

DEPENDENCY_MANIFESTS = frozenset({
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "composer.json", "composer.lock", "go.mod", "go.sum", "Cargo.toml",
    "Cargo.lock", "Gemfile", "Gemfile.lock", "requirements.txt", "Pipfile",
    "poetry.lock", "pyproject.toml", "setup.py", "pom.xml", "build.gradle",
    "build.gradle.kts", "gradle.lockfile",
})

_MANIFEST_NAMES = frozenset(m.lower() for m in DEPENDENCY_MANIFESTS)

LOCK_SUFFIXES = (".lock", "-lock.json", "lockfile", "lock.yaml")

TEST_EXTENSIONS = frozenset({
    ".py", ".go", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".rb", ".java",
    ".php",
})

TEST_MARKERS = (
    "test_", "_test", ".test.", ".spec.", "tests/", "spec/", "__tests__/",
)

DOC_EXTENSIONS = frozenset({".md", ".rst", ".txt"})

CONFIG_EXTENSIONS = frozenset({".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"})

INFRA_MARKERS = (
    "Dockerfile", "docker-compose", ".terraform", ".github/workflows/",
    ".gitlab-ci", "Jenkinsfile",
)


def _is_generated(path: str, base: str, generated: list[str]) -> bool:
    for pattern in generated:
        if (
            (pattern.endswith("/")
             and (path == pattern.rstrip("/") or path.startswith(pattern)))
            or fnmatch.fnmatch(path, pattern)
            or fnmatch.fnmatch(base, pattern)
        ):
            return True
    return False


def _is_infrastructure(path: str, base: str) -> bool:
    return (
        base.startswith("dockerfile")
        or base.startswith(".gitlab-ci")
        or base.startswith("Jenkinsfile")
        or any(marker in path for marker in INFRA_MARKERS)
    )


def _is_test(path: str, base: str) -> bool:
    return any(
        marker in path or base.startswith("test_") or base.endswith("_test")
        for marker in TEST_MARKERS
    )


def classify(paths: list[str], generated: list[str]) -> list[FileClass]:
    """Classify each path into a FileClass, preserving input order.

    Deterministic heuristics, applied in fixed priority order: generated
    patterns, binary extensions, dependency manifests, lock suffixes,
    infrastructure markers, database markers, test markers, documentation,
    config, then a source/unknown fallback. Callers pass the new path for
    renames; the classifier itself need not understand renames.
    """
    out: list[FileClass] = []
    for path in paths:
        base = os.path.basename(path).lower()
        ext = os.path.splitext(path)[1].lower()
        if _is_generated(path, base, generated):
            out.append(FileClass(path, "generated", "matches generated pattern"))
            continue
        if ext in BINARY_EXTENSIONS:
            out.append(FileClass(path, "binary", f"binary extension {ext}"))
            continue
        if base in _MANIFEST_NAMES:
            kind: ChangeKind = "lock" if (
                "lock" in base or base.endswith(LOCK_SUFFIXES)
                or base.endswith(("go.sum", ".lock"))
            ) else "dependency"
            out.append(FileClass(path, kind, "dependency manifest"))
            continue
        if path.endswith(LOCK_SUFFIXES):
            out.append(FileClass(path, "lock", "lock file"))
            continue
        if _is_infrastructure(path, base):
            out.append(FileClass(path, "infrastructure", "infrastructure marker"))
            continue
        if ext == ".sql" or "/migrations/" in path or "migration" in base:
            out.append(FileClass(path, "database", "SQL or migration"))
            continue
        if _is_test(path, base) and ext in TEST_EXTENSIONS:
            out.append(FileClass(path, "test", "test marker"))
            continue
        if ext in DOC_EXTENSIONS or "docs/" in path:
            out.append(FileClass(path, "documentation", "documentation extension"))
            continue
        if ext in CONFIG_EXTENSIONS or "config/" in path:
            out.append(FileClass(path, "config", "config extension"))
            continue
        if ext:
            out.append(FileClass(path, "source", f"source extension {ext}"))
        else:
            out.append(FileClass(path, "unknown", "no recognizable category"))
    return out