"""Data contracts for the ai-review pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
Category = Literal[
    "BUG", "SECURITY", "PERFORMANCE", "CONCURRENCY", "DATA_INTEGRITY",
    "API_COMPAT", "ERROR_HANDLING", "RESOURCE", "EDGE_CASE",
    "MAINTAINABILITY", "TESTING", "CONFIG", "OTHER",
]
Decision = Literal["PASS", "BLOCK", "WARN"]
FileStatus = Literal["added", "modified", "deleted", "renamed"]
ChangeKind = Literal[
    "source", "test", "config", "documentation", "database", "infrastructure",
    "dependency", "generated", "lock", "binary", "unknown",
]


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    changed_new_lines: set[int] = field(default_factory=set)


@dataclass
class StagedChange:
    path: str
    status: FileStatus
    old_path: str | None = None
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)
    stat_added: int = 0
    stat_removed: int = 0


@dataclass(frozen=True)
class Evidence:
    kind: str
    detail: str


@dataclass
class ProfileEntry:
    name: str
    confidence: float
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class RepoProfile:
    languages: list[ProfileEntry] = field(default_factory=list)
    frameworks: list[ProfileEntry] = field(default_factory=list)
    databases: list[ProfileEntry] = field(default_factory=list)
    infrastructure: list[ProfileEntry] = field(default_factory=list)

    def all_names(self) -> list[str]:
        out: list[str] = []
        for group in (self.languages, self.frameworks, self.databases, self.infrastructure):
            out.extend(e.name for e in group)
        return out


@dataclass
class FileClass:
    path: str
    kind: ChangeKind
    reason: str


@dataclass
class ContextBundle:
    path: str
    hunk: Hunk
    before: str
    after: str
    containing_symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    name: str
    command: list[str]
    exit_code: int
    output: str
    auto_detected: bool = False


@dataclass
class Finding:
    severity: Severity
    category: Category
    file: str
    line: int | None
    title: str
    description: str
    evidence: str
    recommendation: str
    confidence: float
    is_pre_existing: bool = False
    hard_block: bool = False


@dataclass
class ReviewResult:
    decision: Decision
    summary: str
    issues: list[Finding] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)


@dataclass(frozen=True)
class PromptPayload:
    system: str
    user: str
    json_schema: dict
    corrective: bool = False


@dataclass
class RawLLMResponse:
    text: str
    provider: str
    endpoint: str
    duration_s: float
    model: str = "auto"


@dataclass
class ReviewContext:
    profile: RepoProfile
    changes: list[StagedChange]
    request_too_large: bool = False
    message: str = ""