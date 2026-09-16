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
    with pytest.raises(ParseError, match="invalid JSON"):
        parse_llm_json('{"decision": PASS}')


def test_parse_missing_required_field_raises():
    with pytest.raises(ParseError, match="schema validation"):
        parse_llm_json('{"summary": "no decision key", "issues": []}')


def test_parse_invalid_severity_message():
    blob = '{"decision": "PASS", "summary": "s", "issues": [{"severity": "NOPE", "category": "BUG", "file": "a", "line": 1, "title": "t", "description": "d", "evidence": "e", "recommendation": "r", "confidence": 0.5}]}'
    with pytest.raises(ParseError, match="invalid severity 'NOPE'"):
        parse_llm_json(blob)
