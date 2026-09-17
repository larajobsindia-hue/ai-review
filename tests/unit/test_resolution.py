"""Unit tests for the deterministic unresolved-reference gate.

Each test writes a small repository (definitions on disk) plus a staged change
whose hunks describe the added lines, then asserts what the gate reports. The
emphasis is on the false-positive guard rails: framework globals, same-scope
definitions, ignored generated trees and non-added lines must never block.
"""
import pytest

from ai_review.models import Hunk, StagedChange
from ai_review.resolution import scan_unresolved


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _change(rel, lines, start=1, status="added", binary=False):
    """A staged change whose added lines are *lines* (the file is on disk)."""
    return StagedChange(
        path=rel,
        status=status,
        is_binary=binary,
        hunks=[Hunk(
            old_start=0, old_count=0, new_start=start, new_count=len(lines),
            added_lines=list(lines),
            changed_new_lines=set(range(start, start + len(lines))),
        )],
        stat_added=len(lines),
    )


def _deleted_change(rel, lines):
    """A staged deletion: *lines* are the file's removed lines."""
    return StagedChange(
        path=rel,
        status="deleted",
        hunks=[Hunk(
            old_start=1, old_count=len(lines), new_start=0, new_count=0,
            removed_lines=list(lines),
        )],
        stat_removed=len(lines),
    )


# -- the reported bug: a Laravel route without the User import ------------------

WEB_PHP = """<?php

use Illuminate\\Support\\Facades\\Route;

Route::get('/', function () {
    $user = User::all();
    return view('welcome');
});
"""

USER_PHP = """<?php

namespace App\\Models;

class User
{
    protected $table = 'users';
}
"""


@pytest.fixture
def laravel(tmp_path):
    _write(tmp_path, "composer.json", '{"require": {"laravel/framework": "^10"}}')
    _write(tmp_path, "app/Models/User.php", USER_PHP)
    _write(tmp_path, "routes/web.php", WEB_PHP)
    return tmp_path


def test_missing_use_in_laravel_route_blocks(laravel):
    change = _change("routes/web.php", WEB_PHP.splitlines())
    findings = scan_unresolved(str(laravel), [change])
    assert len(findings) == 1
    finding = findings[0]
    assert finding.title == "Unresolved reference: User"
    assert finding.file == "routes/web.php"
    assert finding.line == 6
    assert finding.category == "BUG"
    assert finding.severity == "HIGH"
    assert finding.confidence == 1.0
    assert finding.hard_block is True
    assert finding.is_pre_existing is False
    assert finding.evidence == "$user = User::all();"
    assert "use App\\Models\\User;" in finding.recommendation
    assert "app/Models/User.php" in finding.description


def test_framework_facade_without_import_is_not_flagged(laravel):
    # Route has no definition in the repository (it lives in vendor/), so the
    # gate cannot distinguish it from a framework global alias: never a finding.
    findings = scan_unresolved(str(laravel), [_change("routes/web.php", WEB_PHP.splitlines())])
    assert [f.title for f in findings] == ["Unresolved reference: User"]


def test_imported_symbol_is_not_flagged(laravel):
    lines = [
        "<?php",
        "",
        "use App\\Models\\User;",
        "",
        "Route::get('/', fn () => User::all());",
    ]
    _write(laravel, "routes/other.php", "\n".join(lines) + "\n")
    assert scan_unresolved(str(laravel), [_change("routes/other.php", lines)]) == []


def test_php_group_use_alias_is_bound(laravel):
    lines = ["<?php", "use App\\Models\\{User as Account};", "$u = Account::all();"]
    _write(laravel, "routes/group.php", "\n".join(lines) + "\n")
    assert scan_unresolved(str(laravel), [_change("routes/group.php", lines)]) == []


def test_absolute_php_reference_is_not_flagged(laravel):
    lines = ["<?php", "$u = \\App\\Models\\User::all();"]
    _write(laravel, "routes/abs.php", "\n".join(lines) + "\n")
    assert scan_unresolved(str(laravel), [_change("routes/abs.php", lines)]) == []


def test_allowlist_suppresses_finding(laravel):
    change = _change("routes/web.php", WEB_PHP.splitlines())
    assert scan_unresolved(str(laravel), [change], allowlist=["User"]) == []


def test_severity_is_configurable(laravel):
    change = _change("routes/web.php", WEB_PHP.splitlines())
    findings = scan_unresolved(str(laravel), [change], severity="CRITICAL")
    assert findings[0].severity == "CRITICAL"


def test_only_added_lines_are_checked(laravel):
    # The reference is pre-existing (line 1) and the change adds an unrelated
    # line, so the gate must stay silent.
    change = _change("routes/web.php", ["$totals = [];"], start=7, status="modified")
    assert scan_unresolved(str(laravel), [change]) == []


def test_duplicate_references_report_once(laravel):
    lines = ["$a = User::all();", "$b = User::all();"]
    _write(laravel, "routes/dup.php", "\n".join(lines) + "\n")
    findings = scan_unresolved(str(laravel), [_change("routes/dup.php", lines)])
    assert len(findings) == 1
    assert findings[0].line == 1


def test_comment_lines_are_ignored(laravel):
    lines = ["// User::all()", "# User::all()"]
    _write(laravel, "routes/comments.php", "\n".join(lines) + "\n")
    assert scan_unresolved(str(laravel), [_change("routes/comments.php", lines)]) == []


def test_deleted_and_binary_changes_are_skipped(laravel):
    deleted = _change("routes/web.php", WEB_PHP.splitlines(), status="deleted")
    binary = _change("routes/web.php", WEB_PHP.splitlines(), binary=True)
    assert scan_unresolved(str(laravel), [deleted, binary]) == []


def test_blade_and_ambient_ts_are_exempt(laravel):
    blade = _change("resources/views/welcome.blade.php", ["{{ User::all() }}"])
    assert scan_unresolved(str(laravel), [blade]) == []


def test_ignored_trees_do_not_define_symbols(laravel):
    # A same-named class in a generated tree must not make the reference look
    # resolvable; it must also not be reported as a project symbol.
    _write(laravel, "vendor/acme/User.php", "<?php\nnamespace Acme;\nclass User {}\n")
    change = _change("routes/web.php", WEB_PHP.splitlines())
    assert [f.title for f in scan_unresolved(
        str(laravel), [change], ignore=["vendor/"])] == ["Unresolved reference: User"]


def test_wildcard_import_makes_the_file_unanalysable(laravel):
    lines = ["<?php", "from x import *", "User::all();"]
    _write(laravel, "routes/star.py", "\n".join(lines) + "\n")
    # A .py file referencing a PHP-style symbol: the wildcard import alone
    # (plus the unknown extension mix) must keep the gate quiet.
    assert scan_unresolved(str(laravel), [_change("routes/star.py", lines)]) == []


# -- other languages -----------------------------------------------------------


def test_python_missing_import_across_packages(tmp_path):
    _write(tmp_path, "src/models/user.py", "class User:\n    table = 'users'\n")
    body = "from src.api import helpers\n\n\ndef listing():\n    return User.objects.all()\n"
    _write(tmp_path, "src/api/views.py", body)
    findings = scan_unresolved(str(tmp_path), [_change("src/api/views.py", body.splitlines())])
    assert len(findings) == 1
    assert findings[0].title == "Unresolved reference: User"
    assert "from src.models.user import User" in findings[0].recommendation


def test_python_imported_symbol_is_not_flagged(tmp_path):
    _write(tmp_path, "src/models/user.py", "class User:\n    table = 'users'\n")
    body = "from src.models.user import User\n\n\ndef listing():\n    return User.objects.all()\n"
    _write(tmp_path, "src/api/views.py", body)
    assert scan_unresolved(str(tmp_path), [_change("src/api/views.py", body.splitlines())]) == []


def test_python_local_class_is_not_flagged(tmp_path):
    body = "class Session:\n    pass\n\n\ndef build():\n    return Session()\n"
    _write(tmp_path, "app/session.py", body)
    assert scan_unresolved(str(tmp_path), [_change("app/session.py", body.splitlines())]) == []


def test_python_builtin_exception_is_not_flagged(tmp_path):
    body = "def f(x):\n    raise ValueError(x)\n"
    _write(tmp_path, "app/validate.py", body)
    _write(tmp_path, "app/errors.py", "class ValueError(Exception):\n    pass\n")
    assert scan_unresolved(str(tmp_path), [_change("app/validate.py", body.splitlines())]) == []


def test_typescript_missing_component_import(tmp_path):
    _write(tmp_path, "src/components/UserCard.tsx",
           "export function UserCard({ user }: Props) {\n  return <div />;\n}\n")
    body = "export function Page() {\n  return <UserCard user={user} />;\n}\n"
    _write(tmp_path, "src/routes/index.tsx", body)
    findings = scan_unresolved(str(tmp_path), [_change("src/routes/index.tsx", body.splitlines())])
    assert len(findings) == 1
    assert findings[0].title == "Unresolved reference: UserCard"
    assert "src/components/UserCard.tsx" in findings[0].recommendation


def test_javascript_require_binding_resolves(tmp_path):
    _write(tmp_path, "src/lib/Store.js", "export class Store {}\n")
    body = "const { Store } = require('../lib/Store');\n\nStore.open();\n"
    _write(tmp_path, "src/app.js", body)
    assert scan_unresolved(str(tmp_path), [_change("src/app.js", body.splitlines())]) == []


def test_java_missing_import_blocks(tmp_path):
    _write(tmp_path, "src/main/java/com/x/UserService.java",
           "package com.x;\n\npublic class UserService {\n}\n")
    body = ("package com.x.web;\n\n"
            "public class Api {\n"
            "    private UserService service = new UserService();\n"
            "}\n")
    _write(tmp_path, "src/main/java/com/x/web/Api.java", body)
    findings = scan_unresolved(
        str(tmp_path), [_change("src/main/java/com/x/web/Api.java", body.splitlines())])
    assert len(findings) == 1
    assert findings[0].title == "Unresolved reference: UserService"


def test_java_same_package_is_not_flagged(tmp_path):
    _write(tmp_path, "src/main/java/com/x/UserService.java",
           "package com.x;\n\npublic class UserService {\n}\n")
    body = ("package com.x;\n\n"
            "public class Api {\n"
            "    private UserService service = new UserService();\n"
            "}\n")
    _write(tmp_path, "src/main/java/com/x/Api.java", body)
    assert scan_unresolved(
        str(tmp_path), [_change("src/main/java/com/x/Api.java", body.splitlines())]) == []


def test_go_same_package_type_is_not_flagged(tmp_path):
    _write(tmp_path, "svc/types.go", "package svc\n\ntype Request struct{}\n")
    body = "package svc\n\nfunc Handle(r Request) error {\n\treturn nil\n}\n"
    _write(tmp_path, "svc/handler.go", body)
    assert scan_unresolved(str(tmp_path), [_change("svc/handler.go", body.splitlines())]) == []


def test_csharp_child_namespace_is_not_flagged(tmp_path):
    _write(tmp_path, "MyApp/Models/User.cs",
           "namespace MyApp.Models;\n\npublic class User { }\n")
    body = "namespace MyApp;\n\npublic class Program\n{\n    public User Current() => new User();\n}\n"
    _write(tmp_path, "MyApp/Program.cs", body)
    assert scan_unresolved(str(tmp_path), [_change("MyApp/Program.cs", body.splitlines())]) == []


def test_csharp_missing_import_blocks(tmp_path):
    _write(tmp_path, "Other/User.cs", "namespace Other;\n\npublic class User { }\n")
    body = "namespace MyApp;\n\npublic class Program\n{\n    public User Current() => new User();\n}\n"
    _write(tmp_path, "MyApp/Program.cs", body)
    findings = scan_unresolved(str(tmp_path), [_change("MyApp/Program.cs", body.splitlines())])
    assert [f.title for f in findings] == ["Unresolved reference: User"]


def test_ambient_dts_declarations_resolve(tmp_path):
    body = "export function Page() {\n  return <Widget label=\"x\" />;\n}\n"
    _write(tmp_path, "src/routes/index.tsx", body)
    _write(tmp_path, "src/globals.d.ts", "declare const Widget: () => unknown;\n")
    assert scan_unresolved(str(tmp_path), [_change("src/routes/index.tsx", body.splitlines())]) == []


def test_unsupported_extension_is_never_scanned(tmp_path):
    _write(tmp_path, "lib/user.rb", "class User\nend\n")
    body = "User.all\n"
    _write(tmp_path, "app/handler.rb", body)
    assert scan_unresolved(str(tmp_path), [_change("app/handler.rb", body.splitlines())]) == []


ROUTE_WITH_IMPORT = (
    "<?php\n\nuse App\\Models\\User;\n\nRoute::get('/', fn () => User::all());\n"
)


def test_deleting_the_only_definition_blocks(tmp_path):
    """Deleting the file that declared ``User`` turns the reference into a break.

    Alone, the route change yields nothing (no ``User`` exists anywhere in the
    repository, so the gate has no evidence). Adding the deletion supplies it.
    """
    lines = WEB_PHP.splitlines()
    _write(tmp_path, "routes/web.php", WEB_PHP)
    route_only = _change("routes/web.php", lines)
    assert scan_unresolved(str(tmp_path), [route_only]) == []

    findings = scan_unresolved(str(tmp_path), [
        _deleted_change("app/Models/User.php", USER_PHP.splitlines()), route_only,
    ])
    assert [f.title for f in findings] == ["Unresolved reference: User"]
    assert "deleted" in findings[0].description
    assert "restore" in findings[0].recommendation


def test_deleting_a_duplicate_definition_is_not_flagged(tmp_path):
    lines = ROUTE_WITH_IMPORT.splitlines()
    _write(tmp_path, "app/Models/User.php", USER_PHP)
    _write(tmp_path, "routes/web.php", ROUTE_WITH_IMPORT)
    findings = scan_unresolved(str(tmp_path), [
        _deleted_change("legacy/User.php", USER_PHP.splitlines()),
        _change("routes/web.php", lines),
    ])
    assert findings == []


def test_no_changes_returns_no_findings(tmp_path):
    assert scan_unresolved(str(tmp_path), []) == []


def test_missing_file_is_skipped(tmp_path):
    change = _change("routes/web.php", ["User::all();"])
    assert scan_unresolved(str(tmp_path), [change]) == []
