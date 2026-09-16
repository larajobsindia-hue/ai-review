import json

from ai_review.models import CheckResult, Finding, ReviewResult
from ai_review.output import render, render_json, render_markdown, render_terminal


def _result():
    f = Finding(severity="HIGH", category="BUG", file="a.go", line=3, title="nil deref",
                description="possible nil dereference", evidence="returns nil",
                recommendation="check nil", confidence=0.94)
    return ReviewResult(decision="BLOCK", summary="blocked", issues=[f]), f


def test_terminal_has_result_blocked():
    r, f = _result()
    text = render_terminal(r, profile=None, meta={"repo": "x", "branch": "main", "files": 1, "added": 3, "removed": 0})
    assert "COMMIT BLOCKED" in text
    assert "nil deref" in text
    assert "94%" in text
    assert "a.go:3" in text


def test_json_is_valid_and_shapes():
    r, f = _result()
    data = json.loads(render_json(r))
    assert data["decision"] == "BLOCK"
    assert data["issues"][0]["file"] == "a.go"


def test_markdown_has_finding():
    r, f = _result()
    md = render_markdown(r, profile=None, meta={})
    assert "## Issues" in md
    assert "nil deref" in md


def test_render_dispatch():
    r, _ = _result()
    assert render(r, fmt="json").lstrip().startswith("{")
    assert "COMMIT BLOCKED" in render(r, fmt="terminal")


def test_terminal_warn_label():
    r, _ = _result()
    r.decision = "WARN"
    text = render_terminal(r, profile=None, meta={})
    assert "RESULT: WARNING" in text
    assert "COMMIT BLOCKED" not in text


def test_terminal_pass_label_appends_summary_when_clean():
    r = ReviewResult(decision="PASS", summary="all clear")
    text = render_terminal(r, profile=None, meta={})
    assert "RESULT: PASS" in text
    assert "all clear" in text
    assert text.rstrip().endswith("all clear")


def test_terminal_block_with_no_issues_omits_summary():
    r = ReviewResult(decision="BLOCK", summary="blocked hard")
    text = render_terminal(r, profile=None, meta={})
    assert "RESULT: COMMIT BLOCKED" in text
    assert "blocked hard" not in text


def test_terminal_renders_checks_pass_and_fail():
    r = ReviewResult(decision="WARN", summary="s")
    checks = [CheckResult(name="ruff", command=["ruff", "check"], exit_code=0, output=""),
              CheckResult(name="pytest", command=["pytest"], exit_code=1, output="boom")]
    text = render_terminal(r, profile=None, meta={"checks": checks})
    assert "✓ ruff" in text
    assert "✗ pytest" in text


def test_terminal_renders_detected_and_duration():
    r = ReviewResult(decision="PASS", summary="ok")
    text = render_terminal(r, profile=None,
                           meta={"detected": ["python", "pytest"], "duration_s": 3.24})
    assert "Detected: python, pytest" in text
    assert "Duration: 3.2s" in text


def test_terminal_handles_missing_meta_and_null_line():
    r, _ = _result()
    r.issues[0].line = None
    text = render_terminal(r, profile=None, meta=None)
    assert "a.go:?" in text
    assert "Repository:" not in text
    assert "Changes:" not in text


def test_json_includes_checks_array():
    r = ReviewResult(decision="PASS", summary="ok",
                     checks=[CheckResult(name="ruff", command=["ruff", "check"],
                                         exit_code=0, output="clean")])
    data = json.loads(render_json(r))
    assert data["checks"][0]["name"] == "ruff"
    assert data["checks"][0]["command"] == ["ruff", "check"]
    assert data["checks"][0]["exit_code"] == 0


def test_json_issue_fields():
    r, _ = _result()
    data = json.loads(render_json(r))
    issue = data["issues"][0]
    assert issue["severity"] == "HIGH"
    assert issue["category"] == "BUG"
    assert issue["title"] == "nil deref"
    assert issue["description"] == "possible nil dereference"
    assert issue["evidence"] == "returns nil"
    assert issue["recommendation"] == "check nil"
    assert issue["confidence"] == 0.94
    assert issue["is_pre_existing"] is False
    assert issue["line"] == 3


def test_markdown_checks_ok_and_fail():
    r = ReviewResult(decision="PASS", summary="ok",
                     checks=[CheckResult(name="ruff", command=["ruff"], exit_code=0, output=""),
                             CheckResult(name="pytest", command=["pytest"], exit_code=2, output="")])
    md = render_markdown(r, profile=None, meta={})
    assert "- OK: `ruff`" in md
    assert "- FAIL: `pytest`" in md


def test_markdown_table_row_and_detected():
    r, _ = _result()
    md = render_markdown(r, profile=None, meta={"detected": ["python"]})
    assert "# AI Review: BLOCK" in md
    assert "| HIGH | BUG | a.go | 3 | 0.94 | nil deref |" in md
    assert "## Detected" in md
    assert "python" in md


def test_markdown_null_line_renders_dash():
    r, _ = _result()
    r.issues[0].line = None
    md = render_markdown(r, profile=None, meta={})
    assert "| HIGH | BUG | a.go | - | 0.94 | nil deref |" in md


def test_render_dispatch_markdown_and_unknown_falls_back_to_terminal():
    r, _ = _result()
    assert render(r, fmt="markdown", profile=None, meta={}).startswith("# AI Review: BLOCK")
    assert "COMMIT BLOCKED" in render(r, fmt="bogus")
