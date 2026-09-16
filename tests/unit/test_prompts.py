import dataclasses

import pytest

from ai_review.models import Evidence, ProfileEntry, RepoProfile, StagedChange
from ai_review.prompts import (
    MAX_DIFF_CHARS,
    OUTPUT_JSON_SCHEMA,
    PromptBuilder,
    format_diff,
    prompt_files_ok,
    prompt_version,
)

SYSTEM = "You are a senior software engineer performing a pre-commit review.\nReturn ONLY valid JSON matching the supplied schema.\n"
REVIEW = "# Review Task\nReview only the staged diff.\n"
GENERIC = "# Detected Technologies\n{{technology_summary}}\n"


@pytest.fixture
def prompt_dir(tmp_path):
    tech = tmp_path / "technology"
    tech.mkdir()
    (tmp_path / "system.md").write_text(SYSTEM, encoding="utf-8")
    (tmp_path / "review.md").write_text(REVIEW, encoding="utf-8")
    (tech / "generic.md").write_text(GENERIC, encoding="utf-8")
    return tmp_path


def _go_profile():
    return RepoProfile(languages=[ProfileEntry("Go", 0.98, [Evidence("manifest", "go.mod")])])


def test_output_schema_shape():
    assert OUTPUT_JSON_SCHEMA["required"] == ["decision", "summary", "issues"]
    sev = OUTPUT_JSON_SCHEMA["properties"]["issues"]["items"]["properties"]["severity"]["enum"]
    assert sev == ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


def test_build_includes_only_detected_langs(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    profile = _go_profile()
    payload = builder.build(profile=profile, changes=[], diff_text="", context_text="")
    assert "Go" in payload.user
    assert "PHP" not in payload.user
    assert "0.98" in payload.user


def test_build_tracks_technology_markers(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    profile = _go_profile()
    payload = builder.build(profile, [], "d", "")
    assert payload.system.startswith("You are a senior")


def test_build_is_deterministic_and_defaults_corrective_false(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    profile = _go_profile()
    changes = [StagedChange(path="a.go", status="modified", stat_added=2, stat_removed=1)]
    first = builder.build(profile, changes, "DIFF", "CTX")
    second = builder.build(profile, changes, "DIFF", "CTX")
    assert first.system == second.system
    assert first.user == second.user
    assert first.json_schema is OUTPUT_JSON_SCHEMA
    assert first.corrective is False


def test_build_includes_changes_diff_and_context(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    profile = _go_profile()
    changes = [StagedChange(path="a.go", status="modified", stat_added=2, stat_removed=1)]
    payload = builder.build(profile, changes, "DIFFBODY", "CTXBODY")
    assert "- a.go [modified] +2/-1" in payload.user
    assert "DIFFBODY" in payload.user
    assert "CTXBODY" in payload.user
    assert "```json\n" in payload.user
    assert "'decision'" in payload.user
    assert "'is_pre_existing'" in payload.user


def test_build_empty_context_shows_placeholder(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    payload = builder.build(_go_profile(), [], "d", "")
    assert "(none supplied)" in payload.user


def test_build_rules_section_only_when_provided(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    payload = builder.build(_go_profile(), [], "d", "", rules="")
    assert "# Repository Review Rules" not in payload.user
    whitespace = builder.build(_go_profile(), [], "d", "", rules="  \n ")
    assert "# Repository Review Rules" not in whitespace.user
    with_rules = builder.build(_go_profile(), [], "d", "", rules="\n  Never block on style.  \n")
    assert "# Repository Review Rules" in with_rules.user
    assert "Never block on style." in with_rules.user


def test_build_tech_template_override(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    template = "CUSTOM TECH LAYER\n{{technology_summary}}\nEND CUSTOM"
    payload = builder.build(_go_profile(), [], "d", "", tech_template=template)
    assert "CUSTOM TECH LAYER" in payload.user
    assert "END CUSTOM" in payload.user
    assert "# Detected Technologies" not in payload.user
    assert "Go" in payload.user


def test_build_all_profile_groups_and_empty_profile(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    profile = RepoProfile(
        languages=[ProfileEntry("Go", 0.98, [Evidence("manifest", "go.mod")])],
        frameworks=[ProfileEntry("React", 0.75, [Evidence("package", "package.json")])],
        databases=[ProfileEntry("PostgreSQL", 0.55, [Evidence("compose", "docker-compose.yml")])],
        infrastructure=[ProfileEntry("Kubernetes", 0.60, [Evidence("marker", "k8s/deployment.yaml")])],
    )
    payload = builder.build(profile, [], "d", "")
    for name in ("Go", "React", "PostgreSQL", "Kubernetes"):
        assert name in payload.user
    empty = builder.build(RepoProfile(), [], "d", "")
    assert "- none detected" in empty.user


def test_prompt_version_deterministic(prompt_dir):
    assert prompt_version(str(prompt_dir)) == prompt_version(str(prompt_dir))
    assert len(prompt_version(str(prompt_dir))) == 16


def test_prompt_version_changes_with_any_asset(prompt_dir):
    base = prompt_version(str(prompt_dir))
    (prompt_dir / "review.md").write_text(REVIEW + "extra\n", encoding="utf-8")
    assert prompt_version(str(prompt_dir)) != base
    (prompt_dir / "review.md").write_text(REVIEW, encoding="utf-8")
    (prompt_dir / "technology" / "generic.md").write_text(
        GENERIC + "extra\n", encoding="utf-8")
    assert prompt_version(str(prompt_dir)) != base


def test_prompt_files_ok_all_present(prompt_dir):
    ok, missing = prompt_files_ok(str(prompt_dir))
    assert ok is True
    assert missing == []


def test_prompt_files_ok_reports_missing(prompt_dir):
    (prompt_dir / "review.md").unlink()
    ok, missing = prompt_files_ok(str(prompt_dir))
    assert ok is False
    assert missing == ["review.md"]
    (prompt_dir / "technology" / "generic.md").unlink()
    (prompt_dir / "system.md").unlink()
    ok, missing = prompt_files_ok(str(prompt_dir))
    assert missing == ["system.md", "review.md", "technology/generic.md"]


def _mixed_changes():
    return [
        StagedChange(path="a.go", status="modified", stat_added=3, stat_removed=1),
        StagedChange(path="logo.png", status="added", is_binary=True),
        StagedChange(path="new.py", status="renamed", old_path="old.py", stat_added=4, stat_removed=4),
        StagedChange(path="gone.txt", status="deleted", stat_added=0, stat_removed=5),
    ]


def test_format_diff_summary_and_body():
    changes = _mixed_changes()
    rendered = format_diff(changes, "---\n+++\n+BODY", _go_profile())
    assert "# Staged Files" in rendered
    assert "# Staged Diff" in rendered
    assert "- a.go [modified] +3/-1" in rendered
    assert "- logo.png [added] [binary]" in rendered
    assert "- new.py [renamed] (was old.py) +4/-4" in rendered
    assert "- gone.txt [deleted] +0/-5" in rendered
    assert "---\n+++\n+BODY" in rendered


def test_format_diff_rename_binary_line():
    change = StagedChange(path="logo.png", status="renamed", old_path="old.png", is_binary=True)
    rendered = format_diff([change], "d", _go_profile())
    assert "- logo.png [renamed] (was old.png) [binary]" in rendered


def test_format_diff_empty_changes():
    rendered = format_diff([], "d", _go_profile())
    assert "(none)" in rendered
    assert "# Staged Files" in rendered
    assert "# Staged Diff\nd" in rendered


def test_format_diff_under_budget_keeps_full_body():
    short = "some diff\nwith lines\n"
    rendered = format_diff([], short, _go_profile())
    assert "truncated" not in rendered
    assert short in rendered


def test_format_diff_truncation_marks_cut():
    assert MAX_DIFF_CHARS == 30_000
    body = "x" * MAX_DIFF_CHARS + "OVERFLOW_TAIL"
    rendered = format_diff([], body, _go_profile())
    assert "…[truncated 13 chars]" in rendered
    assert body[:MAX_DIFF_CHARS] in rendered
    assert "OVERFLOW_TAIL" not in rendered


def test_format_diff_at_exact_budget_no_marker():
    at_cap = "x" * MAX_DIFF_CHARS
    rendered = format_diff([], at_cap, _go_profile())
    assert "truncated" not in rendered
    assert at_cap in rendered


def test_format_diff_is_deterministic():
    changes = _mixed_changes()
    body = "y" * (MAX_DIFF_CHARS + 7)
    assert format_diff(changes, body, _go_profile()) == format_diff(changes, body, _go_profile())


def test_build_payload_supports_corrective_retry_transport(prompt_dir):
    builder = PromptBuilder(str(prompt_dir))
    payload = builder.build(_go_profile(), [], "d", "")
    assert payload.corrective is False
    corrective = dataclasses.replace(
        payload,
        system=payload.system + "\n\nYour last output was not valid JSON matching the supplied schema. "
        "Return only the JSON object.",
        corrective=True,
    )
    assert corrective.corrective is True
    assert corrective.user == payload.user
    assert corrective.json_schema == payload.json_schema