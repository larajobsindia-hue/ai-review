from ai_review.detector import detect
from ai_review.profile import profile_from_names


def test_detect_go(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n\ngo 1.22\n")
    (tmp_path / "main.go").write_text("package main\n")
    prof = detect(["main.go"], str(tmp_path))
    go_lang = [l for l in prof.languages if l.name == "Go"]
    assert go_lang and go_lang[0].confidence >= 0.9
    assert go_lang[0].evidence


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
    assert {e.name for e in scoped.languages} == {"TypeScript"}
    assert {e.name for e in scoped.frameworks} == {"React"}


def test_detect_unknown(tmp_path):
    (tmp_path / "notes.txt").write_text("hi\n")
    prof = detect([], str(tmp_path))
    assert prof.languages == []


def test_detect_extension_confidence_tiers(tmp_path):
    three = detect(["a.py", "b.py", "c.py"], str(tmp_path))
    py3 = [l for l in three.languages if l.name == "Python"]
    assert py3 and py3[0].confidence == 0.90
    assert py3[0].evidence and all(e.kind == "extension" for e in py3[0].evidence)

    one = detect(["a.py"], str(tmp_path))
    py1 = [l for l in one.languages if l.name == "Python"]
    assert py1 and py1[0].confidence == 0.80
    assert py1[0].evidence and all(e.kind == "extension" for e in py1[0].evidence)


def test_detect_sql_only_reports_no_database(tmp_path):
    prof = detect(["migrations/001_init.sql"], str(tmp_path))
    assert [l.name for l in prof.languages] == ["SQL"]
    assert prof.databases == []


def test_detect_postgres_from_docker_compose(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: postgres:16\n")
    prof = detect(["docker-compose.yml"], str(tmp_path))
    dbs = [d for d in prof.databases if d.name == "PostgreSQL"]
    assert dbs and dbs[0].confidence == 0.55
    assert dbs[0].evidence and all(e.kind == "derived" for e in dbs[0].evidence)


def test_detect_kubernetes_from_changed_path(tmp_path):
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "deployment.yaml").write_text("apiVersion: apps/v1\n")
    prof = detect(["k8s/deployment.yaml"], str(tmp_path))
    k8s = [i for i in prof.infrastructure if i.name == "Kubernetes"]
    assert k8s and k8s[0].confidence == 0.55
    assert k8s[0].evidence and any(e.kind == "marker" for e in k8s[0].evidence)