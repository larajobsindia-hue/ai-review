"""Monorepo scoping: restrict a profile to the languages and frameworks matched by paths."""
from __future__ import annotations

import os

from ai_review.models import ProfileEntry, RepoProfile

LANG_EXT: dict[str, tuple[str, ...]] = {
    "PHP": (".php",),
    "JavaScript": (".js", ".mjs", ".cjs", ".jsx"),
    "TypeScript": (".ts", ".tsx"),
    "Python": (".py",),
    "Go": (".go",),
    "Java": (".java",),
    "Rust": (".rs",),
    "Ruby": (".rb",),
    "C#": (".cs",),
    "SQL": (".sql",),
}

LANG_MANIFESTS: dict[str, tuple[str, ...]] = {
    "PHP": ("composer.json",),
    "JavaScript": ("package.json", ".nvmrc"),
    "TypeScript": ("tsconfig.json", "tsconfig.app.json"),
    "Python": ("pyproject.toml", "requirements.txt", "Pipfile", "poetry.lock", "setup.py"),
    "Go": ("go.mod",),
    "Java": ("pom.xml", "build.gradle", "build.gradle.kts"),
    "Rust": ("Cargo.toml",),
    "Ruby": ("Gemfile",),
    "C#": (".csproj",),
}

# framework -> owning languages (framework kept when any owner language is kept)
FRAMEWORK_OWNERS: dict[str, tuple[str, ...]] = {
    "Laravel": ("PHP",), "Symfony": ("PHP",),
    "React": ("JavaScript", "TypeScript"),
    "Vue": ("JavaScript", "TypeScript"),
    "Angular": ("TypeScript",),
    "Next.js": ("JavaScript", "TypeScript"),
    "Express": ("JavaScript",),
    "Django": ("Python",), "Flask": ("Python",), "FastAPI": ("Python",),
    "Spring": ("Java",),
    "Gin": ("Go",),
}


def _keeps_language(path: str, entry: ProfileEntry) -> bool:
    """True when the basename of path carries an extension/manifest of the entry."""
    base = os.path.basename(path)
    ext = "." + base.rsplit(".", 1)[1].lower() if "." in base and not base.startswith(".") else ""
    if ext in LANG_EXT.get(entry.name, ()):
        return True
    return base in LANG_MANIFESTS.get(entry.name, ())


def profile_from_names(profile: RepoProfile, names: list[str]) -> RepoProfile:
    """Restrict a profile to the languages matched by names and their owned frameworks."""
    if not names:
        return profile
    kept = {e.name for e in profile.languages if any(_keeps_language(n, e) for n in names)}
    return RepoProfile(
        languages=[e for e in profile.languages if e.name in kept],
        frameworks=[e for e in profile.frameworks
                    if not FRAMEWORK_OWNERS.get(e.name)
                    or bool(kept.intersection(FRAMEWORK_OWNERS[e.name]))],
        databases=profile.databases,
        infrastructure=profile.infrastructure,
    )