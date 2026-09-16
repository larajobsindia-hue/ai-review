"""Evidence-based technology detection driven by changed paths and manifest files."""
from __future__ import annotations

import os
from pathlib import Path

from ai_review.models import Evidence, ProfileEntry, RepoProfile

MANIFEST_WEIGHT = 0.98
EXT_3_PLUS_WEIGHT = 0.90
EXT_1_2_WEIGHT = 0.80
DERIVED_WEIGHT = 0.55

LANGUAGE_RULES: list[tuple[str, list[str], list[str]]] = [
    # (language, manifest filenames, changed-file extensions)
    ("PHP", ["composer.json"], [".php"]),
    ("JavaScript", ["package.json", ".nvmrc"], [".js", ".mjs", ".cjs"]),
    ("TypeScript", ["tsconfig.json", "tsconfig.app.json"], [".ts", ".tsx"]),
    ("Python", ["pyproject.toml", "requirements.txt", "Pipfile", "poetry.lock", "setup.py"], [".py"]),
    ("Go", ["go.mod"], [".go"]),
    ("Java", ["pom.xml", "build.gradle", "build.gradle.kts"], [".java"]),
    ("Rust", ["Cargo.toml"], [".rs"]),
    ("Ruby", ["Gemfile"], [".rb"]),
    ("C#", [".csproj"], [".cs"]),
    ("SQL", [], [".sql"]),
]

FRAMEWORK_RULES: list[tuple[str, str, str]] = [
    # (framework, manifest, needle searched in manifest text)
    ("Laravel", "composer.json", "laravel/framework"),
    ("Symfony", "composer.json", "symfony/symfony"),
    ("React", "package.json", '"react"'),
    ("Vue", "package.json", '"vue"'),
    ("Angular", "package.json", "@angular/core"),
    ("Next.js", "package.json", "next"),
    ("Express", "package.json", "express"),
    ("Django", "pyproject.toml", "django"),
    ("Flask", "pyproject.toml", "flask"),
    ("FastAPI", "pyproject.toml", "pydantic"),
    ("Spring", "pom.xml", "spring-boot"),
    ("Gin", "go.mod", "gin-gonic"),
]

DATABASE_RULES: list[tuple[str, list[str]]] = [
    ("PostgreSQL", ["postgres", "postgresql", "pgvector"]),
    ("MySQL", ["mysql"]),
    ("MongoDB", ["mongodb", "mongo"]),
    ("Redis", ["redis"]),
    ("SQLite", ["sqlite"]),
]

INFRA_RULES: list[tuple[str, list[str]]] = [
    ("Docker", ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"]),
    ("Terraform", [".tf"]),
    ("Kubernetes", ["k8s/", "deployment.yaml", "service.yaml", "values.yaml"]),
    ("GitHub Actions", [".github/workflows/"]),
]


def _manifest_text(repo_dir: str, manifest: str) -> str | None:
    p = Path(repo_dir) / manifest
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return None
    return None


def _extension_counts(paths: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        counts[ext] = counts.get(ext, 0) + 1
    return counts


def _detected_languages(repo_dir: str, ext_counts: dict[str, int]) -> list[ProfileEntry]:
    out: list[ProfileEntry] = []
    for lang, manifests, exts in LANGUAGE_RULES:
        manifest_hits = [m for m in manifests if (Path(repo_dir) / m).is_file()]
        matched = {e: n for e, n in ext_counts.items() if e in exts}
        total = sum(matched.values())
        if manifest_hits:
            confidence = MANIFEST_WEIGHT
        elif total >= 3:
            confidence = EXT_3_PLUS_WEIGHT
        elif total >= 1:
            confidence = EXT_1_2_WEIGHT
        else:
            continue
        evidence = [Evidence("manifest", m) for m in manifest_hits]
        evidence.extend(Evidence("extension", f"{n}x {e}") for e, n in matched.items())
        out.append(ProfileEntry(lang, confidence, evidence))
    return out


def _detected_frameworks(repo_dir: str) -> list[ProfileEntry]:
    out: list[ProfileEntry] = []
    for fw, manifest, needle in FRAMEWORK_RULES:
        text = _manifest_text(repo_dir, manifest)
        if text is not None and needle in text:
            out.append(ProfileEntry(
                fw, MANIFEST_WEIGHT,
                [Evidence("manifest", f"{manifest} contains {needle}")]))
    return out


def _detected_databases(repo_dir: str) -> list[ProfileEntry]:
    out: list[ProfileEntry] = []
    for db, needles in DATABASE_RULES:
        hits: list[str] = []
        for mf in ("docker-compose.yml", "docker-compose.yaml", "values.yaml"):
            text = _manifest_text(repo_dir, mf)
            if not text:
                continue
            low = text.lower()
            for needle in needles:
                if needle in low:
                    hits.append(f"{mf} mentions {needle}")
        for _, manifests, _ in LANGUAGE_RULES:
            for manifest in manifests:
                text = _manifest_text(repo_dir, manifest)
                if not text:
                    continue
                low = text.lower()
                for needle in needles:
                    if needle in low:
                        hits.append(f"{manifest} mentions {needle}")
        if hits:
            out.append(ProfileEntry(db, DERIVED_WEIGHT,
                                    [Evidence("derived", h) for h in hits[:3]]))
    return out


def _detected_infrastructure(repo_dir: str, change_paths: list[str]) -> list[ProfileEntry]:
    out: list[ProfileEntry] = []
    for name, markers in INFRA_RULES:
        hits = [m for m in markers
                if (Path(repo_dir) / m).is_file()
                or any(m in p for p in change_paths)]
        if hits:
            out.append(ProfileEntry(name, DERIVED_WEIGHT,
                                    [Evidence("marker", h) for h in hits]))
    return out


def detect(change_paths: list[str], repo_dir: str) -> RepoProfile:
    """Build a RepoProfile from changed paths and manifest files under repo_dir."""
    ext_counts = _extension_counts(change_paths)
    return RepoProfile(
        languages=_detected_languages(repo_dir, ext_counts),
        frameworks=_detected_frameworks(repo_dir),
        databases=_detected_databases(repo_dir),
        infrastructure=_detected_infrastructure(repo_dir, change_paths),
    )