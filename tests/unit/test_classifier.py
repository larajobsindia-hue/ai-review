from ai_review.classifier import classify


def _kinds(paths, generated=()):
    return [(c.path, c.kind) for c in classify(paths, list(generated))]


def test_classify_examples():
    classes = {c.path: c.kind for c in classify(
        ["app/service.go", "service_test.go", "Dockerfile", "migration.sql",
         "package.json", "README.md", "dist/bundle.js", "package-lock.json",
         "logo.png"], generated=["dist/", "build/"],
    )}
    assert classes["app/service.go"] == "source"
    assert classes["service_test.go"] == "test"
    assert classes["Dockerfile"] == "infrastructure"
    assert classes["migration.sql"] == "database"
    assert classes["package.json"] == "dependency"
    assert classes["README.md"] == "documentation"
    assert classes["dist/bundle.js"] == "generated"
    assert classes["package-lock.json"] == "lock"
    assert classes["logo.png"] == "binary"


def test_classify_preserves_input_order():
    paths = ["z.py", "a.md", "m.py"]
    assert [c.path for c in classify(paths, [])] == paths


def test_classify_generated_fnmatch_path():
    assert _kinds(["static/js/app.min.js"], ["*.min.js"]) == [
        ("static/js/app.min.js", "generated")]


def test_classify_generated_fnmatch_base():
    assert _kinds(["static/js/bundle.js"], ["bundle*"]) == [
        ("static/js/bundle.js", "generated")]


def test_classify_generated_trailing_slash():
    assert _kinds(["dist"], ["dist/"]) == [("dist", "generated")]
    assert _kinds(["build/b.js"], ["build/"]) == [("build/b.js", "generated")]


def test_classify_generated_no_match_falls_through():
    assert _kinds(["src/keep.go"], ["dist/", "*.min.js"]) == [
        ("src/keep.go", "source")]


def test_classify_generated_precedes_binary():
    assert _kinds(["dist/logo.png"], ["dist/"]) == [
        ("dist/logo.png", "generated")]


def test_classify_binary_non_png():
    assert _kinds(["assets/font.woff2"]) == [("assets/font.woff2", "binary")]
    assert _kinds(["photos/photo.JPEG"]) == [("photos/photo.JPEG", "binary")]


def test_classify_manifest_dependency_and_lock():
    assert _kinds(["Cargo.toml"]) == [("Cargo.toml", "dependency")]
    assert _kinds(["go.sum"]) == [("go.sum", "lock")]
    assert _kinds(["gradle.lockfile"]) == [("gradle.lockfile", "lock")]


def test_classify_manifest_in_subdirectory():
    assert _kinds(["services/web/package.json"]) == [
        ("services/web/package.json", "dependency")]
    assert _kinds(["services/web/package-lock.json"]) == [
        ("services/web/package-lock.json", "lock")]


def test_classify_standalone_lock_suffix():
    assert _kinds(["foo.lock"]) == [("foo.lock", "lock")]
    assert _kinds(["addon-lock.json"]) == [("addon-lock.json", "lock")]
    assert _kinds(["delete.lock.yaml"]) == [("delete.lock.yaml", "lock")]


def test_classify_infrastructure_markers():
    assert _kinds([".gitlab-ci.yml"]) == [(".gitlab-ci.yml", "infrastructure")]
    assert _kinds([".github/workflows/ci.yml"]) == [
        (".github/workflows/ci.yml", "infrastructure")]
    assert _kinds(["k8s/docker-compose.yml"]) == [
        ("k8s/docker-compose.yml", "infrastructure")]
    assert _kinds(["Jenkinsfile"]) == [("Jenkinsfile", "infrastructure")]
    assert _kinds(["Dockerfile.prod"]) == [("Dockerfile.prod", "infrastructure")]


def test_classify_database():
    assert _kinds(["migration.sql"]) == [("migration.sql", "database")]
    assert _kinds(["db/migrations/0001_init.sql"]) == [
        ("db/migrations/0001_init.sql", "database")]
    assert _kinds(["db/migrations/0002_alter.sql.j2"]) == [
        ("db/migrations/0002_alter.sql.j2", "database")]
    assert _kinds(["db/migrations/notes.txt"]) == [
        ("db/migrations/notes.txt", "database")]
    assert _kinds(["tools/migration_tool.py"]) == [
        ("tools/migration_tool.py", "database")]


def test_classify_tests():
    for path in ["tests/test_model.py", "src/spec/model.spec.js",
                 "src/__tests__/x.ts", "src/test_helpers.go",
                 "src/foo.test.tsx", "src/foo_test.rb"]:
        assert _kinds([path]) == [(path, "test")], path


def test_classify_test_marker_non_source_ext_falls_through():
    assert _kinds(["tests/README.md"]) == [
        ("tests/README.md", "documentation")]


def test_classify_documentation():
    assert _kinds(["docs/guide.md"]) == [("docs/guide.md", "documentation")]
    assert _kinds(["README.rst"]) == [("README.rst", "documentation")]
    assert _kinds(["notes.txt"]) == [("notes.txt", "documentation")]


def test_classify_config():
    assert _kinds(["config/settings.json"]) == [
        ("config/settings.json", "config")]
    assert _kinds(["deploy/values.yaml"]) == [("deploy/values.yaml", "config")]
    assert _kinds(["nginx.conf"]) == [("nginx.conf", "config")]


def test_classify_source_default():
    out = _kinds(["app/service.go", "frontend/index.ts", "scripts/run.sh"])
    assert [kind for _, kind in out] == ["source", "source", "source"]


def test_classify_unknown():
    assert _kinds(["LICENSE"]) == [("LICENSE", "unknown")]
    assert _kinds(["RUNME"]) == [("RUNME", "unknown")]
    assert _kinds([""]) == [("", "unknown")]