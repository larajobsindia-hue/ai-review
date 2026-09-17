"""LLM JSON parsing with schema validation."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError

from ai_review.models import Finding, ReviewResult, StaticAssessment

#: The AI's verdict vocabulary for a static finding (spec §27).
VALID_VERDICTS = frozenset({
    "confirmed", "likely_true", "uncertain", "likely_false_positive", "false_positive",
})


class ParseError(RuntimeError):
    """Raised when LLM output contains no parseable, schema-conforming JSON."""


class _IssueModel(BaseModel):
    severity: str
    category: str
    file: str
    line: int | None = None
    title: str
    description: str
    evidence: str = ""
    recommendation: str = ""
    confidence: float
    is_pre_existing: bool = False
    #: References a static finding's ``id`` (validated against the run later).
    related_static_finding_id: str | None = None


class _AssessmentModel(BaseModel):
    finding_id: str
    verdict: str
    reason: str = ""


class _ReviewModel(BaseModel):
    decision: str
    summary: str
    issues: list[_IssueModel] = Field(default_factory=list)
    static_assessments: list[_AssessmentModel] = Field(default_factory=list)


def _loads_dict(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _extract_json(text: str) -> dict:
    """Return the first decodable JSON object in *text* (fenced JSON preferred).

    Falls back to a ``raw_decode`` scan so braces inside surrounding prose or
    inside string values cannot reject a conforming object.
    """
    fences = re.search(r"```(?:json)?\s*(\{.*?\})```", text, re.S)
    if fences:
        candidate = _loads_dict(fences.group(1))
        if isinstance(candidate, dict):
            return candidate
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _end = decoder.raw_decode(text, idx)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise ParseError("no JSON object found in LLM output")


def parse_llm_json(text: str, schema: dict | None = None) -> ReviewResult:
    data = _extract_json(text)
    try:
        model = _ReviewModel.model_validate(data)
    except ValidationError as exc:
        raise ParseError(f"LLM output failed schema validation: {exc}") from exc

    valid_severity = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
    for issue in model.issues:
        if issue.severity not in valid_severity:
            raise ParseError(f"invalid severity {issue.severity!r}")
    for assessment in model.static_assessments:
        if assessment.verdict not in VALID_VERDICTS:
            raise ParseError(f"invalid static verdict {assessment.verdict!r}")

    findings = [
        Finding(severity=i.severity, category=i.category, file=i.file, line=i.line,
                title=i.title, description=i.description, evidence=i.evidence,
                recommendation=i.recommendation, confidence=float(i.confidence),
                is_pre_existing=i.is_pre_existing,
                related_static_finding_id=i.related_static_finding_id)
        for i in model.issues
    ]
    return ReviewResult(
        decision=model.decision, summary=model.summary, issues=findings,
        static_assessments=[
            StaticAssessment(finding_id=a.finding_id, verdict=a.verdict, reason=a.reason)
            for a in model.static_assessments
        ],
    )
