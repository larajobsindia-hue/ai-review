"""Normalization: explicit per-tool maps, preserved originals, stable ids."""
from ai_review.static_analysis import normalizer as n


def test_normalize_severity_uses_the_supplied_map_only():
    mapping = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}
    assert n.normalize_severity("ERROR", mapping) == "HIGH"
    assert n.normalize_severity("error", mapping) == "HIGH"      # case-insensitive
    assert n.normalize_severity("weird", mapping) == "MEDIUM"    # default
    assert n.normalize_severity(None, mapping, default="INFO") == "INFO"


def test_confidence_map_and_unknown_fallback():
    assert n.confidence_from("HIGH") == 0.9
    assert n.confidence_from("medium") == 0.6
    assert n.confidence_from(None, default=0.5) == 0.5


def test_infer_category_hits_keywords_and_falls_back():
    assert n.infer_category("php.lang.security.sql-injection") == "SECURITY"
    assert n.infer_category(None, "race condition") == "CONCURRENCY"
    assert n.infer_category("style/indent") == "MAINTAINABILITY"
    assert n.infer_category(None, "", default="OTHER") == "OTHER"


def test_normalize_message_is_order_independent_for_dedup():
    assert n.normalize_message("  Potential   SQL\nInjection ") == "potential sql injection"
    assert n.normalize_message("") == ""


def test_stable_id_is_tool_independent_and_position_sensitive():
    a = n.stable_id("app/x.php", 42, "SECURITY", "Potential SQL injection")
    b = n.stable_id("app/x.php", 42, "SECURITY", "Potential   SQL injection")
    c = n.stable_id("app/x.php", 43, "SECURITY", "Potential SQL injection")
    assert a == b and a != c and len(a) == 12


def test_make_finding_preserves_original_and_sets_provenance():
    f = n.make_finding(tool="semgrep", rule_id="php.lang.security.sql-injection",
                       fingerprint="fp1", severity="HIGH", original_severity="ERROR",
                       file="app/Models/User.php", line=42,
                       message="Potential SQL injection.", confidence=0.9,
                       detected_by=["semgrep"])
    assert f.source == "static_analysis"
    assert f.tool == "semgrep"
    assert f.rule_id == "php.lang.security.sql-injection"
    assert f.original_severity == "ERROR"
    assert f.original_message == "Potential SQL injection."
    assert f.category == "SECURITY"
    assert f.detected_by == ["semgrep"]
    assert f.confidence == 0.9
    assert f.id == n.stable_id("app/Models/User.php", 42, "SECURITY",
                               "Potential SQL injection.")
    assert f.hard_block is False          # static evidence never blocks by itself


def test_make_finding_requires_no_recommendation_from_the_tool():
    f = n.make_finding(tool="ruff", severity="LOW", file="a.py", line=1,
                       message="unused import")
    assert f.recommendation
    assert f.evidence == ""
    assert f.title == "unused import"


def test_worst_severity_picks_the_higher_rank():
    assert n.worst_severity("LOW", "CRITICAL") == "CRITICAL"
    assert n.worst_severity("HIGH", "HIGH") == "HIGH"
