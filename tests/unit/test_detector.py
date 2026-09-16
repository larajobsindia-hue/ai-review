from ai_review.detector import detect
from ai_review.profile import profile_from_names


def test_detect_go(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text("package main\n")
    prof = detect([], str(tmp_path))
    go_lang = [l for l in prof.languages if l.name == "Go"]
    assert go_lang and go_lang[0].confidence >= 0.9


def test_detect_typescript_monorepo_scoping(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18.0.0"}}\n')
    (tmp_path / "tsconfig.json").write_text("{}\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.tsx").write_text("export default () => null;\n")
    prof = detect([], str(tmp_path))
    names = {l.name: l for l in prof.languages}
    assert "TypeScript" in names and names["TypeScript"].confidence >= 0.8
    fw = [f for f in prof.frameworks if f.name == "React"]
    assert fw and fw[0].confidence >= 0.7

    scoped = profile_from_names(prof, ["index.tsx"])
    assert scoped.languages and all(e.name in ("TypeScript", "JavaScript") for e in scoped.languages)
    assert scoped.frameworks and all(e.name == "React" for e in scoped.frameworks)


def test_detect_unknown(tmp_path):
    (tmp_path / "notes.txt").write_text("hi\n")
    prof = detect([], str(tmp_path))
    assert prof.languages == []