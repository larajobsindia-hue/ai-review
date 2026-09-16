from ai_review.diff import parse_unified_diff, attach_hunks, changed_new_ranges
from ai_review.models import StagedChange

DIFF = """diff --git a/a.go b/a.go
index 111..222 100644
--- a/a.go
+++ b/a.go
@@ -1,3 +1,4 @@
 package main
 func main() {
+    println(1)
     println(2)
 }
"""

def test_parse_unified_diff():
    parsed = parse_unified_diff(DIFF)
    assert "a.go" in parsed
    h = parsed["a.go"][0]
    assert (h.old_start, h.old_count) == (1, 3)
    assert (h.new_start, h.new_count) == (1, 4)
    assert 3 in h.changed_new_lines
    assert 2 not in h.changed_new_lines

def test_attach_hunks():
    changes = [StagedChange(path="a.go", status="modified")]
    attach_hunks(changes, parse_unified_diff(DIFF))
    assert changes[0].hunks[0].changed_new_lines == {3}

def test_multiple_files_and_hunks():
    diff = """diff --git a/a.go b/a.go
index 1..2 100644
--- a/a.go
+++ b/a.go
@@ -1 +1,2 @@
 package main
+// new
diff --git a/b.py b/b.py
index 3..4 100644
--- a/b.py
+++ b/b.py
@@ -5,2 +6,3 @@
 old_line
-removed_line
+new_line
@@ -10 +12,2 @@
 const X = 1
+const Y = 2
 """
    parsed = parse_unified_diff(diff)
    assert set(parsed) == {"a.go", "b.py"}
    h = parsed["a.go"][0]
    assert (h.old_start, h.old_count) == (1, 1)
    assert (h.new_start, h.new_count) == (1, 2)
    assert h.added_lines == ["// new"]
    assert h.changed_new_lines == {2}
    ha, hb = parsed["b.py"]
    assert (ha.old_start, ha.new_start) == (5, 6)
    assert ha.added_lines == ["new_line"]
    assert ha.removed_lines == ["removed_line"]
    assert ha.changed_new_lines == {7}
    assert (hb.old_start, hb.new_start) == (10, 12)
    assert hb.added_lines == ["const Y = 2"]
    assert hb.changed_new_lines == {13}

def test_changed_new_ranges_union():
    diff = """diff --git a/a.go b/a.go
index 1..2 100644
--- a/a.go
+++ b/a.go
@@ -1 +1,2 @@
 package main
+// first
@@ -5 +7,2 @@
 func old() {
+	return 1
 """
    changes = [StagedChange(path="a.go", status="modified")]
    attach_hunks(changes, parse_unified_diff(diff))
    assert [h.changed_new_lines for h in changes[0].hunks] == [{2}, {8}]
    assert changed_new_ranges(changes[0]) == {2, 8}

def test_added_and_removed_lines_starting_with_plus_minus():
    diff = """diff --git a/a.go b/a.go
index 1..2 100644
--- a/a.go
+++ b/a.go
@@ -1,2 +1,2 @@
+++x
---y
 """
    parsed = parse_unified_diff(diff)
    h = parsed["a.go"][0]
    assert h.added_lines == ["++x"]
    assert h.removed_lines == ["--y"]

def test_empty_and_header_only_input():
    assert parse_unified_diff("") == {}
    parsed = parse_unified_diff(DIFF.split("@@")[0])
    assert set(parsed.keys()) == {"a.go"}
    assert parsed["a.go"] == []

QUOTED_OCTAL_DIFF = r'''diff --git "a/\346\227\245\346\234\254.go" "b/\346\227\245\346\234\254.go"
index 111..222 100644
--- "a/\346\227\245\346\234\254.go"
+++ "b/\346\227\245\346\234\254.go"
@@ -1 +1,2 @@
 package main
+// nihongo
'''

def test_quoted_octal_path_hunks_attached():
    parsed = parse_unified_diff(QUOTED_OCTAL_DIFF)
    assert set(parsed) == {"日本.go"}
    h = parsed["日本.go"][0]
    assert (h.old_start, h.old_count) == (1, 1)
    assert (h.new_start, h.new_count) == (1, 2)
    assert h.added_lines == ["// nihongo"]
    changes = [StagedChange(path="日本.go", status="modified")]
    attach_hunks(changes, parsed)
    assert changes[0].hunks[0].added_lines == ["// nihongo"]

