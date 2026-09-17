"""Dedup: exact, cross-tool, and the negative case (near is not the same)."""
from ai_review.static_analysis import normalizer as n
from ai_review.static_analysis.deduplicator import dedupe


def _f(**kw):
    base = dict(tool="semgrep", severity="MEDIUM", file="app/x.php", line=42,
                message="Potential SQL injection.")
    base.update(kw)
    return n.make_finding(**base)


def test_exact_within_tool_duplicates_collapse():
    out = dedupe([_f(rule_id="r1"), _f(rule_id="r1")])
    assert len(out) == 1


def test_cross_tool_merge_unions_provenance_and_keeps_one_id():
    first = _f(tool="semgrep", severity="MEDIUM", rule_id="php.security.sql-injection")
    second = _f(tool="phpstan", severity="HIGH", rule_id="argument.type",
                message="Potential   SQL injection.")
    out = dedupe([first, second])
    assert len(out) == 1
    assert out[0].detected_by == ["phpstan", "semgrep"]
    assert out[0].severity == "HIGH"                 # highest severity wins
    # The survivor is the most severe reporter (phpstan); the id is derived from
    # it and is stable across runs (see the order-independence test below).
    assert out[0].tool == "phpstan"
    assert out[0].id == n.stable_id("app/x.php", 42, out[0].category,
                                    "Potential SQL injection.")


def test_same_line_same_rule_with_different_wording_still_merges_per_category():
    out = dedupe([_f(tool="eslint", rule_id="no-eval", message="eval is unsafe"),
                  _f(tool="eslint", rule_id="no-eval", message="do not use eval")])
    assert len(out) == 1                      # one rule == one problem


def test_near_but_distinct_findings_never_merge():
    same_line_other_issue = _f(message="Missing return type declaration.")
    other_line = _f(line=43)
    other_category = _f(category="BUG", message="Value may be null here.")
    out = dedupe([_f(), same_line_other_issue, other_line, other_category])
    assert len(out) == 4


def test_dedup_is_order_independent():
    findings = [_f(rule_id="a"),
                _f(tool="phpstan", severity="HIGH", rule_id="b", message="Other problem."),
                _f(rule_id="a"),
                _f(tool="ruff", file="z.py", line=1, rule_id="c", message="unused")]
    forward = dedupe(list(findings))
    backward = dedupe(list(reversed(findings)))
    assert [(f.file, f.line, f.severity, f.id, f.detected_by) for f in forward] == \
           [(f.file, f.line, f.severity, f.id, f.detected_by) for f in backward]


def test_shared_rule_id_merges_across_tools():
    out = dedupe([_f(tool="a", rule_id="shared.rule", message="one thing"),
                  _f(tool="b", rule_id="shared.rule", message="a totally different wording")])
    assert len(out) == 1


def test_empty_input():
    assert dedupe([]) == []
