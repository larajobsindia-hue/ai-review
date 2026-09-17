"""AnalysisContext: what an analyzer may inspect, and nothing more."""
from ai_review.config import AppConfig
from ai_review.models import RepoProfile, StagedChange
from ai_review.static_analysis.context import (SCOPE_CHANGED_FILES, SCOPE_PROJECT,
                                              build_context, with_config)


def _change(path, status="modified", binary=False):
    return StagedChange(path=path, status=status, is_binary=binary)


def test_changed_paths_filters_deleted_binary_and_missing(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.js").write_text("let x = 1\n")
    ctx = build_context(str(tmp_path), AppConfig(), [
        _change("a.py"),
        _change("b.js"),
        _change("gone.py", status="deleted"),
        _change("blob.py", binary=True),
        _change("never-on-disk.py"),
    ], RepoProfile())
    assert ctx.changed_paths() == ["a.py", "b.js"]
    assert ctx.changed_paths((".py",)) == ["a.py"]
    assert ctx.changed_paths((".sql",)) == []


def test_languages_and_frameworks_and_file_probes(tmp_path):
    (tmp_path / "composer.json").write_text("{}")
    profile = RepoProfile(languages=[])
    ctx = build_context(str(tmp_path), AppConfig(), [], profile)
    assert ctx.languages() == set()
    assert ctx.infrastructure() == set()
    assert ctx.has_file("composer.json") is True
    assert ctx.has_file("missing.json") is False
    assert ctx.has_any(("missing.json", "composer.json")) is True
    assert ctx.has_any(("missing.json",)) is False


def test_build_context_carries_config_and_with_config_replaces_it():
    cfg = AppConfig()
    ctx = build_context("/repo", cfg, [], RepoProfile())
    assert ctx.cfg is cfg.static_analysis
    other = AppConfig().static_analysis
    assert with_config(ctx, other).cfg is other
    assert ctx.cfg is cfg.static_analysis            # original untouched


def test_scope_constants():
    assert (SCOPE_CHANGED_FILES, SCOPE_PROJECT) == ("changed_files", "project")
