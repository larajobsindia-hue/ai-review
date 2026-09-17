import json

import pytest

from ai_review.parser import ParseError, parse_llm_json

GOOD = """```json
{"decision": "BLOCK", "summary": "s", "issues": [
  {"severity": "HIGH", "category": "BUG", "file": "a.go", "line": 3,
   "title": "t", "description": "d", "evidence": "e", "recommendation": "r",
   "confidence": 0.9, "is_pre_existing": false}
]}
```"""

BAD = "I could not produce JSON. Here is prose."


def test_parse_good_fenced_json():
    result = parse_llm_json(GOOD)
    assert result.decision == "BLOCK"
    assert result.issues[0].file == "a.go"
    assert result.issues[0].confidence == pytest.approx(0.9)


def test_parse_plain_json():
    result = parse_llm_json('{"decision": "PASS", "summary": "ok", "issues": []}')
    assert result.decision == "PASS"


def test_parse_bad_raises():
    with pytest.raises(ParseError):
        parse_llm_json(BAD)


def test_parse_invalid_schema_field():
    blob = '{"decision": "PASS", "summary": "s", "issues": [{"severity": "NOPE", "category": "BUG", "file": "a", "line": 1, "title": "t", "description": "d", "evidence": "e", "recommendation": "r", "confidence": 0.5, "is_pre_existing": false}]}'
    with pytest.raises(ParseError):
        parse_llm_json(blob)


def test_parse_fenced_without_json_tag():
    fenced = '```{"decision": "WARN", "summary": "w", "issues": []}```'
    assert parse_llm_json(fenced).decision == "WARN"


def test_parse_fenced_json_tag_tight_close():
    fenced = '```json\n{"decision": "WARN", "summary": "w", "issues": []}```'
    assert parse_llm_json(fenced).decision == "WARN"


def test_parse_json_embedded_in_prose():
    blob = (
        "Sure, here is my review:\n"
        '{"decision": "PASS", "summary": "fine", "issues": []}\n'
        "Hope this helps!"
    )
    assert parse_llm_json(blob).summary == "fine"


def test_parse_missing_issues_defaults_empty():
    result = parse_llm_json('{"decision": "PASS", "summary": "no issues key"}')
    assert result.issues == []


def test_parse_issue_field_defaults():
    blob = '{"decision": "BLOCK", "summary": "s", "issues": [{"severity": "MEDIUM", "category": "OTHER", "file": "f.py", "line": null, "title": "t", "description": "d", "confidence": 1}]}'
    issue = parse_llm_json(blob).issues[0]
    assert issue.evidence == ""
    assert issue.recommendation == ""
    assert issue.line is None
    assert issue.is_pre_existing is False
    assert issue.confidence == pytest.approx(1.0)
    assert isinstance(issue.confidence, float)


def test_parse_malformed_json_raises():
    with pytest.raises(ParseError, match="no JSON object|invalid JSON"):
        parse_llm_json('{"decision": PASS}')


def test_parse_missing_required_field_raises():
    with pytest.raises(ParseError, match="schema validation"):
        parse_llm_json('{"summary": "no decision key", "issues": []}')


def test_parse_invalid_severity_message():
    blob = '{"decision": "PASS", "summary": "s", "issues": [{"severity": "NOPE", "category": "BUG", "file": "a", "line": 1, "title": "t", "description": "d", "evidence": "e", "recommendation": "r", "confidence": 0.5}]}'
    with pytest.raises(ParseError, match="invalid severity 'NOPE'"):
        parse_llm_json(blob)


def test_prose_braces_before_json_recovers():
    blob = 'params like {a: 1} then {"decision": "PASS", "summary": "ok", "issues": []}'
    assert parse_llm_json(blob).decision == "PASS"


def test_trailing_prose_braces_after_fenced_json_recovers():
    blob = '```json\n{"decision": "PASS", "summary": "ok", "issues": []}\n```\nextra prose with {stuff}'
    assert parse_llm_json(blob).decision == "PASS"


def test_brace_inside_string_value_survives():
    blob = '{"decision": "PASS", "summary": "has } brace", "issues": []}'
    result = parse_llm_json(blob)
    assert result.summary == "has } brace"


def test_no_json_at_all_still_raises():
    with pytest.raises(ParseError, match="no JSON object"):
        parse_llm_json("no braces here at all")


def test_invalid_fenced_json_falls_through_to_plain_object():
    blob = '```json\n{oops not json}\n```\n{"decision": "PASS", "summary": "ok", "issues": []}'
    assert parse_llm_json(blob).decision == "PASS"


def test_single_line_fenced_json_uses_fence_branch():
    blob = '```json {"decision": "PASS", "summary": "ok", "issues": []}```'
    assert parse_llm_json(blob).decision == "PASS"


def test_tight_fenced_invalid_json_raises():
    with pytest.raises(ParseError, match="no JSON object"):
        parse_llm_json('```json {oops not json}```')


STATIC_ANSWER = json.dumps({
    "decision": "WARN", "summary": "checked",
    "issues": [{"severity": "HIGH", "category": "SECURITY", "file": "app/x.php",
                "line": 3, "title": "t", "description": "d", "evidence": "e",
                "recommendation": "r", "confidence": 0.9, "is_pre_existing": False,
                "related_static_finding_id": "abc123"}],
    "static_assessments": [{"finding_id": "abc123", "verdict": "confirmed",
                             "reason": "reachable"}],
})


def test_parse_static_assessments_and_links():
    result = parse_llm_json(STATIC_ANSWER)
    assert result.issues[0].related_static_finding_id == "abc123"
    assert result.static_assessments[0].finding_id == "abc123"
    assert result.static_assessments[0].verdict == "confirmed"
    assert result.static_assessments[0].reason == "reachable"


def test_parse_is_backward_compatible_without_static_fields():
    result = parse_llm_json('{"decision": "PASS", "summary": "ok", "issues": []}')
    assert result.static_assessments == []
    assert result.issues == []


def test_parse_issue_without_link_leaves_it_empty():
    result = parse_llm_json(
        '{"decision": "PASS", "summary": "s", "issues": [{"severity": "LOW",'
        ' "category": "OTHER", "file": "a.py", "line": 1, "title": "t",'
        ' "description": "d", "confidence": 0.5}]}')
    assert result.issues[0].related_static_finding_id is None


def test_parse_rejects_unknown_verdict():
    bad = json.dumps({"decision": "PASS", "summary": "s", "issues": [],
                      "static_assessments": [{"finding_id": "a", "verdict": "maybe"}]})
    with pytest.raises(ParseError, match="invalid static verdict"):
        parse_llm_json(bad)


def test_parse_tolerates_malformed_assessments_only_when_absent():
    # An assessment missing its required finding_id is a schema failure, not a
    # silently dropped verdict: the corrective retry gets a chance to fix it.
    bad = json.dumps({"decision": "PASS", "summary": "s", "issues": [],
                      "static_assessments": [{"verdict": "confirmed"}]})
    with pytest.raises(ParseError, match="schema validation"):
        parse_llm_json(bad)
