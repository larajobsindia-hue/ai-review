import subprocess

import pytest

from ai_review.git import collect_staged, git_branch, git_repo_root


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "T")
    return tmp_path


def test_repo_root_and_branch(repo):
    assert git_repo_root(str(repo)) == str(repo.resolve())
    _git(repo, "checkout", "-q", "-b", "feature/x")
    assert git_branch(str(repo)) == "feature/x"


def test_collect_staged_added_modified_deleted(repo):
    (repo / "a.go").write_text("package main\nfunc main() {}\n")
    _git(repo, "add", "a.go")
    _git(repo, "commit", "-qm", "init")
    (repo / "del.go").write_text("package main\n")
    _git(repo, "add", "del.go")
    _git(repo, "commit", "-qm", "del-init")
    (repo / "a.go").write_text("package main\nfunc main() { println(1) }\n")
    (repo / "b.go").write_text("package main\n\nfunc helper() int {\n\treturn 42\n}\n")
    _git(repo, "rm", "-q", "del.go")
    _git(repo, "add", "-A")
    changes = collect_staged(str(repo))
    statuses = {c.path: c.status for c in changes}
    assert statuses == {"a.go": "modified", "b.go": "added", "del.go": "deleted"}


def test_collect_staged_rename(repo):
    (repo / "old.go").write_text("package main\n")
    _git(repo, "add", "old.go")
    _git(repo, "commit", "-qm", "init")
    (repo / "new.go").write_text("package main\n")
    _git(repo, "add", "-A")
    _git(repo, "rm", "-q", "old.go") if (repo / "old.go").exists() else None
    changes = collect_staged(str(repo))
    renamed = [c for c in changes if c.status == "renamed"]
    assert renamed and renamed[0].path == "new.go"