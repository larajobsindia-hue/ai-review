import os
import subprocess

import pytest

from ai_review.git import collect_staged, git_branch, git_repo_root
from ai_review.security import scan_staged

requires_symlink = pytest.mark.skipif(
    not hasattr(os, "symlink"), reason="filesystem without symlink support"
)


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
    assert renamed[0].old_path == "old.go"
    assert renamed[0].stat_added == 0
    assert renamed[0].stat_removed == 0


def test_collect_staged_binary(repo):
    (repo / "img.png").write_bytes(bytes(range(256)))
    _git(repo, "add", "img.png")
    _git(repo, "commit", "-qm", "img-init")
    (repo / "img.png").write_bytes(bytes(range(256))[::-1])
    _git(repo, "add", "-A")
    changes = collect_staged(str(repo))
    binary = [c for c in changes if c.path == "img.png"]
    assert binary
    assert binary[0].status == "modified"
    assert binary[0].is_binary is True
    assert binary[0].stat_added == 0


@requires_symlink
def test_collect_staged_typechange_last_with_secret(repo):
    (repo / "a.go").write_text("package main\n")
    os.symlink("target", repo / "zreadme.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    (repo / "a.go").write_text("package main\nfunc main() { println(1) }\n")
    (repo / "zreadme.md").unlink()
    (repo / "zreadme.md").write_text("token: supers3cretvalue\n")
    _git(repo, "add", "-A")
    changes = collect_staged(str(repo))
    statuses = {c.path: c.status for c in changes}
    assert statuses == {"a.go": "modified", "zreadme.md": "modified"}
    findings = scan_staged(str(repo), changes)
    assert any(f.file == "zreadme.md" and f.hard_block for f in findings)


@requires_symlink
def test_collect_staged_typechange_midstream(repo):
    (repo / "a_first.go").write_text("package main\n")
    os.symlink("target", repo / "m_mid.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    (repo / "a_first.go").write_text("package main\nfunc x() {}\n")
    (repo / "m_mid.md").unlink()
    (repo / "m_mid.md").write_text("# regular file now\n")
    (repo / "z_last.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    changes = collect_staged(str(repo))
    statuses = {c.path: c.status for c in changes}
    assert statuses == {
        "a_first.go": "modified",
        "m_mid.md": "modified",
        "z_last.py": "added",
    }
