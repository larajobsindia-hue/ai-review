import re

from ai_review.models import StagedChange
from ai_review.security import (
    SECRET_PATTERNS,
    redact_text,
    scan_staged,
    should_exclude,
)

AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
GITHUB_TOKEN = "ghp_1234567890123456789012345678901234"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwfQ.signaturetokenvalue123"


def test_redact_aws_key():
    redacted, kinds = redact_text(f"aws_access_key_id={AWS_ACCESS_KEY}")
    assert AWS_ACCESS_KEY not in redacted
    assert "aws" in kinds


def test_redact_aws_secret_access_key_value():
    redacted, kinds = redact_text(f"aws_secret_access_key={AWS_SECRET}")
    assert AWS_SECRET not in redacted
    assert "[REDACTED:aws:" in redacted
    assert kinds == ["aws"]


def test_redact_dedupes_kinds_across_patterns():
    text = f"{AWS_ACCESS_KEY}\naws_secret_access_key={AWS_SECRET}\n"
    redacted, kinds = redact_text(text)
    assert AWS_ACCESS_KEY not in redacted
    assert AWS_SECRET not in redacted
    assert kinds == ["aws"]


def test_redact_private_key_block():
    pem_snippet = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpQIBAAKCA\n"
        "-----END RSA PRIVATE KEY-----"
    )
    redacted, kinds = redact_text(pem_snippet)
    assert "MIIEpQIBAAKCA" not in redacted
    assert "private-key" in kinds


def test_redact_bearer_token():
    text = "Authorization: Bearer abc.def.ghijklmnopqrstuvwxyz1234567890"
    redacted, kinds = redact_text(text)
    assert "ghijklmnop" not in redacted
    assert "bearer" in kinds


def test_redact_jwt():
    redacted, kinds = redact_text(JWT)
    assert JWT not in redacted
    assert "[REDACTED:jwt:" in redacted
    assert "jwt" in kinds


def test_redact_connstr():
    text = "postgres://user:pass@localhost:5432/appdb"
    redacted, kinds = redact_text(text)
    assert "pass@localhost" not in redacted
    assert "[REDACTED:connstr:" in redacted
    assert "connstr" in kinds


def test_redact_password_keyword_pins_threshold():
    text = 'password = "hunter22"\n'
    redacted, kinds = redact_text(text)
    assert "hunter22" not in redacted
    assert "password" in kinds


def test_redact_password_keyword_leaves_short_values():
    text = "pwd = short\n"
    redacted, kinds = redact_text(text)
    assert text == redacted
    assert kinds == []


def test_redact_custom_patterns():
    custom = [("custom", re.compile(r"FOO\d+"))]
    text = "FOO123 bar"
    redacted, kinds = redact_text(text, patterns=custom)
    assert "FOO123" not in redacted
    assert "bar" in redacted
    assert kinds == ["custom"]


def test_redact_empty_patterns_falls_back_to_defaults():
    redacted, kinds = redact_text(f"key={AWS_ACCESS_KEY}", patterns=[])
    assert AWS_ACCESS_KEY not in redacted
    assert kinds == ["aws"]


def test_redact_placeholders_survive_a_second_pass():
    redacted, _ = redact_text(f"aws_access_key_id={AWS_ACCESS_KEY}")
    again, kinds = redact_text(redacted)
    assert again == redacted
    assert kinds == []


def test_redact_output_uses_deterministic_sha_placeholders():
    text = f"x={AWS_ACCESS_KEY}"
    one, _ = redact_text(text)
    two, _ = redact_text(text)
    assert one == two
    assert re.search(r"\[REDACTED:aws:[0-9a-f]{10}\]$", one)
    assert "AKIAIOSFODNN7EXAMPLE" not in one


def test_secret_patterns_exported():
    assert isinstance(SECRET_PATTERNS, list)
    assert all(isinstance(kind, str) and isinstance(pattern, re.Pattern)
               for kind, pattern in SECRET_PATTERNS)


def test_should_exclude():
    assert should_exclude(".env", [".env", "*.pem"])
    assert should_exclude("secrets/key.pem", [".env", "*.pem"])
    assert not should_exclude("src/main.go", [".env", "*.pem"])


def test_should_exclude_basename_only():
    assert should_exclude("sub/dir/key.pem", ["*.pem"])


def test_should_exclude_dir_suffix():
    assert should_exclude("sub/dir/note.txt", ["sub/dir/"])
    assert should_exclude("sub/dir/src/app.go", ["sub/dir/"])
    assert not should_exclude("sub/dir-other/app.go", ["sub/dir/"])


def test_should_exclude_no_patterns():
    assert not should_exclude("x/y/env.json", [])


def test_scan_staged_finds_hardcoded_key(tmp_path):
    (tmp_path / "app.py").write_text(f'API_KEY = "{GITHUB_TOKEN}"\n')
    change = StagedChange(path="app.py", status="added")
    findings = scan_staged(str(tmp_path), [change])
    assert findings
    assert findings[0].severity == "CRITICAL"
    assert findings[0].category == "SECURITY"
    assert findings[0].confidence == 1.0
    assert findings[0].hard_block is True
    assert findings[0].file == "app.py"
    assert findings[0].line == 1


def test_scan_staged_line_number(tmp_path):
    (tmp_path / "cfg.txt").write_text("hello\nkey=" + AWS_ACCESS_KEY + "\n")
    change = StagedChange(path="cfg.txt", status="modified")
    findings = scan_staged(str(tmp_path), [change])
    assert findings
    assert findings[0].line == 2
    assert findings[0].evidence == AWS_ACCESS_KEY


def test_scan_staged_skips_deleted(tmp_path):
    (tmp_path / "gone.py").write_text(f"key={GITHUB_TOKEN}\n")
    change = StagedChange(path="gone.py", status="deleted")
    assert scan_staged(str(tmp_path), [change]) == []


def test_scan_staged_skips_renamed(tmp_path):
    (tmp_path / "moved.py").write_text(f"key={GITHUB_TOKEN}\n")
    change = StagedChange(path="moved.py", status="renamed", old_path="old.py")
    assert scan_staged(str(tmp_path), [change]) == []


def test_scan_staged_skips_excluded_default(tmp_path):
    (tmp_path / ".env").write_text(f"TOKEN={GITHUB_TOKEN}\n")
    change = StagedChange(path=".env", status="added")
    assert scan_staged(str(tmp_path), [change]) == []


def test_scan_staged_skips_excluded_custom(tmp_path):
    (tmp_path / "notes.txt").write_text(f"TOKEN={GITHUB_TOKEN}\n")
    change = StagedChange(path="notes.txt", status="added")
    assert scan_staged(str(tmp_path), [change], excluded=["*.txt"]) == []


def test_scan_staged_skips_binary(tmp_path):
    (tmp_path / "blob.bin").write_bytes(f"key={GITHUB_TOKEN}".encode())
    change = StagedChange(path="blob.bin", status="added", is_binary=True)
    assert scan_staged(str(tmp_path), [change]) == []


def test_scan_staged_skips_missing_file(tmp_path):
    change = StagedChange(path="missing.txt", status="added")
    assert scan_staged(str(tmp_path), [change]) == []


def test_scan_staged_multiple_changes(tmp_path):
    (tmp_path / "a.py").write_text(f"key={GITHUB_TOKEN}\n")
    (tmp_path / "b.go").write_text("package main\n")
    a = StagedChange(path="a.py", status="added")
    b = StagedChange(path="b.go", status="added")
    findings = scan_staged(str(tmp_path), [a, b])
    assert len(findings) >= 1
    assert all(f.file == "a.py" for f in findings)