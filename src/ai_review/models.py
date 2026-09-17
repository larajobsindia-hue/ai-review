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
ToolStatus = Literal["run", "unavailable", "skipped", "failed"]
StaticVerdict = Literal[
    "confirmed", "likely_true", "uncertain", "likely_false_positive", "false_positive",
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
    # -- static-analysis provenance (spec §18/§19/§26); all defaulted so the
    #    existing constructors are unchanged and AI findings stay source="ai".
    source: str = "ai"                      # "ai" | "static_analysis"
    id: str | None = None                   # stable static id (deduplicator)
    tool: str | None = None
    rule_id: str | None = None
    fingerprint: str | None = None
    original_severity: str | None = None    # the analyzer's own severity string
    original_message: str | None = None     # the analyzer's own message
    detected_by: list[str] = field(default_factory=list)   # merged-tool provenance
    related_static_finding_id: str | None = None           # AI issue -> static id


@dataclass
class ToolResult:
    """Per-tool outcome row of a static-analysis run (spec §15/§23)."""
    name: str
    status: ToolStatus
    exit_code: int | None = None
    error: str = ""
    duration_ms: int = 0


@dataclass
class AnalyzerResult:
    """What one analyzer returns from ``analyze(ctx)`` (upstream §2)."""
    findings: list[Finding] = field(default_factory=list)
    status: ToolStatus = "run"
    exit_code: int | None = None
    error: str = ""
    duration_ms: int = 0


@dataclass
class StaticAnalysisSummary:
    """Everything static analysis produced for one review (spec §23)."""
    findings: list[Finding] = field(default_factory=list)
    tools: list[ToolResult] = field(default_factory=list)
    files_analyzed: int = 0
    duration_ms: int = 0

    def severity_counts(self) -> dict[str, int]:
        """Severity histogram with all five keys present (stable JSON shape)."""
        out = {name: 0 for name in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")}
        for finding in self.findings:
            out[finding.severity] = out.get(finding.severity, 0) + 1
        return out


@dataclass
class StaticAssessment:
    """The AI's verdict on one static finding (spec §26/§27).

    Stored separately from the finding: the original static finding is never
    mutated by an AI verdict.
    """
    finding_id: str
    verdict: StaticVerdict
    reason: str = ""


@dataclass
class ReviewResult:
    decision: Decision
    summary: str
    issues: list[Finding] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    #: Static-analysis evidence. Never merged into ``issues``: static findings
    #: are input to the LLM, not input to the policy gate (design D1).
    static: StaticAnalysisSummary | None = None
    #: Validated AI verdicts on static findings (unknown ids dropped).
    static_assessments: list[StaticAssessment] = field(default_factory=list)


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
