"""Deterministic unresolved-reference gate: a missing import never passes.

The LLM layer is advisory — it may miss a reference that no import binds
(``User::all()`` with no ``use App\\Models\\User;``), and even when it spots one
its confidence decides whether the policy engine blocks. This module is the
language-agnostic backstop: for every *added* line it collects candidate
symbols in resolution positions (a name that is instantiated, statically
accessed, attributed, called, or named in a type position) and asks whether
that name is bound in this file. A name is bound when the file imports it,
defines or assigns it, shares the repository scope it is defined in (same
directory subtree, or the same declared namespace/package), or is a language
builtin. Anything else is reported with ``hard_block=True`` and
``confidence=1.0``, so it blocks the commit offline and regardless of what the
provider answered. The same gate reports the mirror case — a reference whose
only definition this change set deletes — by recovering the removed
declarations from the staged deletion itself.

Two deliberate biases keep the gate from crying wolf on legitimate code:

* Only names *defined somewhere in the repository* are reported. A symbol that
  is not part of the repo (Laravel's ``Route`` facade alias, Python's
  ``Exception``) is never a finding, so framework global-alias conventions
  cannot be mistaken for missing imports.
* All resolution failures here are false *negatives* (a wildcard import, a
  same-directory definition, an allowlisted project global, an unsupported
  extension). A blocked legitimate commit destroys trust in the gate, so the
  checks resolve in favour of "bound".

The scan reads the staged files from the working tree and never the diff, so
the ``review.max_diff_kb`` truncation that limits the prompt cannot hide a
missing import from this gate.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ai_review.models import Finding, StagedChange
from ai_review.security import should_exclude

#: Extensions whose ecosystems bind referenced symbols by name at the top of
#: the file, so an unimported reference is a real NameError/compile error
#: rather than a convention.
CHECKED_EXTENSIONS = frozenset({
    ".php", ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".cs", ".go",
})

#: Suffixes exempted despite a checked extension: Blade templates resolve
#: facades through Laravel's global aliases, and ``d.ts`` files are pure
#: ambient declarations (they *define* globals, they never import them).
EXEMPT_SUFFIXES = (".blade.php", ".d.ts")

#: Names a language exposes without any import. Curated for the languages in
#: :data:`CHECKED_EXTENSIONS`; over-suppression only costs a false negative,
#: so entries are added generously but never for project-shaped names.
BUILTIN_GLOBALS = frozenset({
    # Python builtins (exceptions, singletons)
    "True", "False", "None", "NotImplemented", "Ellipsis",
    "BaseException", "Exception", "ArithmeticError", "AssertionError",
    "AttributeError", "BlockingIOError", "BrokenPipeError", "BufferError",
    "ChildProcessError", "ConnectionError", "ConnectionAbortedError",
    "ConnectionRefusedError", "ConnectionResetError", "EOFError",
    "EnvironmentError", "FileExistsError", "FileNotFoundError",
    "FloatingPointError", "GeneratorExit", "IOError", "ImportError",
    "IndentationError", "IndexError", "InterruptedError", "IsADirectoryError",
    "KeyError", "KeyboardInterrupt", "LookupError", "MemoryError",
    "ModuleNotFoundError", "NameError", "NotADirectoryError",
    "NotImplementedError", "OSError", "OverflowError", "PermissionError",
    "ProcessLookupError", "RecursionError", "ReferenceError", "RuntimeError",
    "StopAsyncIteration", "StopIteration", "SyntaxError", "SystemError",
    "SystemExit", "TabError", "TimeoutError", "TypeError",
    "UnboundLocalError", "UnicodeError", "UnicodeDecodeError",
    "UnicodeEncodeError", "UnicodeTranslateError", "ValueError", "Warning",
    "ZeroDivisionError",
    # Java / Kotlin / C# implicitly-available types
    "System", "String", "Object", "Math", "Integer", "Boolean", "Double",
    "Long", "Float", "Character", "Byte", "Short", "Class", "Enum", "Unit",
    "Any", "Nothing", "Thread", "Runtime", "Throwable", "RuntimeException",
    "Error", "StringBuilder", "StringBuffer", "Comparable", "Iterable",
    "Iterator", "Number", "Void", "Override", "Deprecated", "SuppressWarnings",
    # ECMAScript globals
    "Array", "JSON", "Date", "Promise", "Map", "Set", "WeakMap", "WeakSet",
    "Number", "RegExp", "Symbol", "BigInt", "Reflect", "Proxy", "Intl",
    "URL", "URLSearchParams", "ArrayBuffer", "SharedArrayBuffer", "Atomics",
    "DataView", "Function", "AggregateError", "EvalError", "RangeError",
    "ReferenceError", "SyntaxError", "TypeError", "URIError", "NaN",
    "Infinity", "Uint8Array", "Int8Array", "Uint16Array", "Int16Array",
    "Uint32Array", "Int32Array", "Float32Array", "Float64Array",
})

#: An UpperCamelCase symbol: the cross-language shape of a type, class,
#: namespace head, component, decorator or module-level constant.
_NAME = r"[A-Z][A-Za-z0-9_]*"

#: A dotted/backslashed qualification chain whose head is an :data:`_NAME`
#: (``User``, ``Models\User``, ``App\Models\User``).
_QUALIFIED = rf"\\?{_NAME}(?:[.\\]{_NAME})*"

#: Resolution positions, ordered most to least specific. Each pattern's first
#: group is the referenced symbol (or chain), captured on an added line.
CANDIDATE_PATTERNS: list[re.Pattern] = [
    re.compile(rf"(?<![\w$\\])({_QUALIFIED})\s*::"),          # User::all()
    re.compile(rf"\bnew\s+({_QUALIFIED})\b"),                  # new User(...)
    re.compile(rf"(?<![\w$])({_NAME})\.(?=[A-Za-z_])"),        # User.objects
    re.compile(rf"(?<![\w$])({_NAME})\s*\("),                  # User(...)
    re.compile(rf"(?::|->|extends|implements|instanceof)\s+({_QUALIFIED})\b"),
    re.compile(r"@({_NAME})\b"),                               # @decorator
    re.compile(rf"<({_NAME})\b"),                              # List<User>
    re.compile(rf"\b({_NAME})\s*(?:\[\]|\|)"),                 # User[] / User|None
]

#: Suffixes that only *declare* a symbol (no binding) and are not read as
#: source. Comments cannot reference anything at runtime.
_COMMENT_PREFIXES = ("#", "//", "*", "/*", "*/", "--", "<!--", "%")

_DEFINITION_PATTERNS = [
    re.compile(
        r"\b(?:class|interface|trait|enum|struct|record|module|object|"
        r"protocol|namespace|def|func|function|type|typedef|extension)\s+"
        rf"({_NAME})\b"
    ),
    re.compile(
        rf"\b(?:const|var|let|final|val)\s+(?:static\s+|readonly\s+|mut\s+)*({_NAME})\b"
    ),
    re.compile(rf"^\s*({_NAME})\s*(?::[^=\n]+)?=[^=]", re.M),
]

_NAMESPACE_PATTERN = re.compile(
    r"^\s*(?:namespace|package)\s+([A-Za-z_][\w.\\]*)\s*[;{]?", re.M
)

#: Upper bound on files read while indexing repository definitions.
MAX_SCANNED_FILES = 5_000
#: Files larger than this are never indexed (generated blobs, fixtures).
MAX_SCAN_BYTES = 1_000_000


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _is_exempt(path: str) -> bool:
    return path.endswith(EXEMPT_SUFFIXES)


def _defined_names(content: str) -> set[str]:
    """Every symbol this file defines, declares or assigns (upper-camel only)."""
    names: set[str] = set()
    for pattern in _DEFINITION_PATTERNS:
        names.update(m.group(1) for m in pattern.finditer(content))
    return names


def _namespace_of(content: str, ext: str) -> str:
    """The file's declared namespace/package, or ``""`` when it declares none."""
    if ext not in CHECKED_EXTENSIONS:
        return ""
    match = _NAMESPACE_PATTERN.search(content)
    return match.group(1).rstrip(";{ ") if match else ""


def _split_import_list(clause: str, sep: str) -> set[str]:
    """Bound names in an import clause: ``a, B as C, D`` -> ``{a, C, D}``."""
    names = set()
    for part in clause.replace("(", " ").replace(")", " ").split(sep):
        part = part.strip()
        if not part:
            continue
        name = part.split(" as ")[-1].split(":")[-1].strip().strip("{}[];,")
        if re.fullmatch(_NAME, name) or re.fullmatch(r"[a-z_]\w*", name):
            names.add(name)
    return names


def _imported_names(content: str, ext: str) -> tuple[set[str], bool]:
    """Names bound by this file's imports, plus "a wildcard import exists".

    A wildcard import makes the file unanalysable (any name could be bound),
    so the caller skips it entirely rather than risk a false positive.
    """
    names: set[str] = set()
    wildcard = False

    if ext == ".py":
        for m in re.finditer(r"^\s*import\s+([\w.]+)(?:\s+as\s+(\w+))?", content, re.M):
            names.add(m.group(2) or m.group(1).split(".")[0])
        for m in re.finditer(r"^\s*from\s+[\w.]+\s+import\s+(.+)$", content, re.M):
            if "*" in m.group(1):
                wildcard = True
                continue
            names.update(_split_import_list(m.group(1), ","))

    if ext == ".php":
        for m in re.finditer(r"^\s*use\s+([^\n;]+);", content, re.M):
            body = m.group(1).strip()
            if "(" in body:
                continue  # closure capture (`use ($x)`), not an import
            if "{" in body:  # group form: use App\Models\{User, Post as P};
                _, _, inner = body.partition("{")
                inner = inner.rstrip("}").strip()
                for part in inner.split(","):
                    part = part.strip()
                    if part:
                        names.add(part.split(" as ")[-1].strip())
                continue
            leaf = body.split(" as ")[0].strip().rstrip("\\").split("\\")[-1]
            alias = body.split(" as ")[-1].strip() if " as " in body else leaf
            if alias:
                names.add(alias)

    if ext in (".js", ".jsx", ".ts", ".tsx"):
        for m in re.finditer(r"^\s*import\s+([^\n]+?)\s+from\s+['\"]", content, re.M):
            clause = m.group(1).strip()
            if clause.startswith("*"):
                names.add(clause.split("as")[-1].strip())
            elif "{" in clause:
                head = clause.split("{")[0].strip().rstrip(",").strip()
                if head:
                    names.add(head)
                names.update(_split_import_list(clause.split("{", 1)[1], ","))
            else:
                names.add(clause.split(",")[0].strip())
        for m in re.finditer(
            r"^\s*(?:const|let|var)\s+([^=\n]+?)\s*=\s*require\s*\(", content, re.M
        ):
            clause = m.group(1).strip()
            if "{" in clause:
                inner = clause.split("{", 1)[1].rsplit("}", 1)[0]
                names.update(_split_import_list(inner, ","))
            elif re.fullmatch(_NAME, clause.strip().strip("{};")) or re.fullmatch(
                r"[a-z_]\w*", clause
            ):
                names.add(clause)

    if ext in (".java", ".kt"):
        for m in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+)(?:\.\*)?;?\s*$",
                             content, re.M):
            imported = m.group(1)
            if imported.endswith("*") or ".*" in m.group(0):
                wildcard = True
                continue
            names.add(imported.split(".")[-1])

    if ext == ".cs":
        for m in re.finditer(r"^\s*using\s+([^;\n]+);", content, re.M):
            body = m.group(1).strip()
            if "=" in body:  # alias form: using Users = App\Models\User;
                names.add(body.split("=")[0].strip().split(".")[-1])
            # a plain namespace using binds no name of its own

    if ext == ".go":
        for m in re.finditer(r'^\s*(?:([A-Za-z_]\w*)\s+)?"([^"]+)"', content, re.M):
            if m.group(1):
                names.add(m.group(1))
            else:
                names.add(m.group(2).split("/")[-1])

    return names, wildcard


def _in_repo_scope(ref_path: str, def_path: str, ref_ns: str, def_ns: str) -> bool:
    """Is *def_path* reachable from *ref_path* without an import?

    True when both files declare the same namespace/package, when they sit in
    the same directory (Go packages, Java packages, PHP sibling classes), or
    when the definition lives *under* the referencing file's directory — the
    C# parent-namespace lookup. The reverse direction is intentionally not
    resolved: a Java/Kotlin subpackage, a Go subdirectory and a PHP sibling
    namespace each require an explicit import.
    """
    if ref_ns and def_ns and ref_ns == def_ns:
        return True
    ref_dir = Path(ref_path).parent
    def_dir = Path(def_path).parent
    return ref_dir == def_dir or ref_dir in def_dir.parents


def _dir_ignored(rel_dir: str, patterns: list[str]) -> bool:
    return should_exclude(rel_dir.rstrip("/") + "/", patterns)


def _repo_definitions(
    root: Path, ignore: list[str]
) -> tuple[dict[str, list[tuple[str, str]]], set[str]]:
    """Index upper-camel symbols defined anywhere in the repository.

    Returns ``(defs, ambient)`` where *defs* maps a symbol to its
    ``(relative_path, namespace)`` definitions and *ambient* holds symbols
    declared in ``d.ts`` files, which are global by construction. Generated
    paths in *ignore* (``vendor/``, ``node_modules/``, ...) are skipped, so a
    framework symbol never looks like a project symbol.
    """
    defs: dict[str, list[tuple[str, str]]] = {}
    ambient: set[str] = set()
    visited = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(
            d for d in dirnames if d != ".git" and not _dir_ignored(f"{rel_dir}/{d}", ignore)
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if _is_exempt(rel) and not name.endswith(".d.ts"):
                continue
            if os.path.splitext(name)[1].lower() not in CHECKED_EXTENSIONS:
                continue
            if should_exclude(rel, ignore):
                continue
            if name.endswith(".d.ts"):
                content = _read(path)
                if content is not None:
                    ambient.update(_defined_names(content))
                continue
            if visited >= MAX_SCANNED_FILES:
                return defs, ambient
            content = _read(path)
            if content is None:
                continue
            visited += 1
            namespace = _namespace_of(content, ext=os.path.splitext(name)[1].lower())
            for symbol in _defined_names(content):
                defs.setdefault(symbol, []).append((rel, namespace))
    return defs, ambient


def _added_lines(change: StagedChange):
    """Yield ``(new_line_number, text)`` for each line this change adds."""
    for hunk in change.hunks:
        for line_no, text in zip(sorted(hunk.changed_new_lines), hunk.added_lines):
            yield line_no, text


def _candidates(line: str) -> list[str]:
    """Symbols referenced on *line* in a resolution position (chains decayed to
    their head name, absolute references dropped, comments ignored)."""
    stripped = line.strip()
    if not stripped or stripped.startswith(_COMMENT_PREFIXES):
        return []
    found: list[str] = []
    for pattern in CANDIDATE_PATTERNS:
        for m in pattern.finditer(line):
            raw = m.group(1)
            if raw.startswith("\\"):
                continue  # absolute reference: needs no import
            head = re.split(r"[.\\]", raw)[0]
            if head and head not in found:
                found.append(head)
    return found


def _import_hint(name: str, def_path: str, def_ns: str, ext: str) -> str:
    """A concrete import the developer can paste, per language family."""
    if ext == ".php":
        fqn = f"{def_ns}\\{name}" if def_ns else name
        return f"Add `use {fqn};` to this file, or reference it as `\\{fqn}`."
    if ext == ".py":
        module = def_path[:-3].replace("/", ".").replace(".__init__", "")
        return (f"Add `from {module} import {name}` (or import the module and "
                f"qualify the reference).")
    if ext in (".js", ".jsx", ".ts", ".tsx"):
        return f"Add an import for `{name}` from `{def_path}`."
    if ext == ".go":
        return (f"Import the package that defines `{name}` (`{def_path}`) and "
                f"qualify the reference with it.")
    return (f"Import `{name}` in this file (defined in `{def_path}`), or "
            f"reference it by its fully-qualified name.")


def _removed_definitions(changes: list[StagedChange]) -> dict[str, str]:
    """Symbols whose only definition this change set *deletes*.

    A staged deletion still carries the deleted file's lines in its hunks, so
    the declarations it removed can be recovered without touching git again.
    Renames are ignored: git reports them as ``renamed`` and the new path is
    already indexed from disk.
    """
    removed: dict[str, str] = {}
    for change in changes:
        if change.status != "deleted":
            continue
        if os.path.splitext(change.path)[1].lower() not in CHECKED_EXTENSIONS:
            continue
        for hunk in change.hunks:
            for symbol in _defined_names("\n".join(hunk.removed_lines)):
                removed.setdefault(symbol, change.path)
    return removed


@dataclass(frozen=True)
class _Reference:
    """One added-line reference to a symbol nothing in its own file binds."""

    path: str
    name: str
    line: int
    text: str
    namespace: str
    ext: str


def _unbound_references(
    root: Path, changes: list[StagedChange], allowed: set[str]
) -> list[_Reference]:
    """Added-line references that nothing *inside their own file* binds.

    This is the cheap disqualifying pass: it reads only the staged files and
    filters out the overwhelming majority of references (imported symbols,
    local definitions, builtins, allowlisted globals, files it cannot reason
    about) before the repository is ever walked.
    """
    refs: list[_Reference] = []
    for change in changes:
        if change.status == "deleted" or change.is_binary or _is_exempt(change.path):
            continue
        ext = os.path.splitext(change.path)[1].lower()
        if ext not in CHECKED_EXTENSIONS or not change.hunks:
            continue
        content = _read(root / change.path)
        if content is None:
            continue
        imported, wildcard = _imported_names(content, ext)
        if wildcard:
            continue
        bound = imported | _defined_names(content) | allowed | BUILTIN_GLOBALS
        namespace = _namespace_of(content, ext)
        seen: set[str] = set()
        for line_no, text in _added_lines(change):
            for name in _candidates(text):
                if name in seen or name in bound:
                    continue
                seen.add(name)
                refs.append(_Reference(change.path, name, line_no,
                                       text.strip()[:200], namespace, ext))
    return refs


def scan_unresolved(
    repo_dir: str,
    changes: list[StagedChange],
    *,
    ignore: list[str] | None = None,
    allowlist: list[str] | None = None,
    severity: str = "HIGH",
) -> list[Finding]:
    """Report symbols referenced by added lines that no import binds.

    *ignore* excludes generated trees from the repository index (pass
    ``cfg.generated.ignore``); *allowlist* names project globals the gate must
    never question (pass ``cfg.resolution.allowlist``). Only non-deleted,
    non-binary, non-exempt files are analysed, and findings are deduplicated
    per (file, symbol) at the first added line that references it, so the
    result is deterministic for a given repository state.

    Two shapes are reported: a reference no import binds, and a reference to a
    symbol this same change set deletes the only definition of.

    Each finding is a ``BUG`` with ``confidence=1.0`` and ``hard_block=True``:
    the reference cannot resolve as written, so the commit must not pass, and
    the policy engine is bypassed rather than asked to trust a probability.
    """
    ignore_patterns = list(ignore or [])
    allowed = set(allowlist or [])
    root = Path(repo_dir)
    refs = _unbound_references(root, changes, allowed)
    if not refs:
        return []  # nothing to resolve: never walk the repository
    defs, ambient = _repo_definitions(root, ignore_patterns)
    removed = _removed_definitions(changes)

    findings: list[Finding] = []
    for ref in refs:
        definitions = defs.get(ref.name)
        if definitions:
            if any(_in_repo_scope(ref.path, path, ref.namespace, ns)
                   for path, ns in definitions):
                continue
            def_path, def_ns = min(definitions, key=lambda d: (len(d[0]), d[0]))
            where = (f"it is defined in `{def_path}`"
                     + (f" (namespace `{def_ns}`)" if def_ns else ""))
            recommendation = _import_hint(ref.name, def_path, def_ns, ref.ext)
        else:
            deleted_path = removed.get(ref.name)
            if deleted_path is None:
                # Not a repository symbol: a framework global, a builtin we
                # did not list, or a typo. Not enough evidence to block.
                continue
            where = (f"its only definition, `{deleted_path}`, is being "
                     f"deleted here")
            recommendation = (f"Update this reference, or restore "
                              f"`{deleted_path}`.")
        findings.append(Finding(
            severity=severity,
            category="BUG",
            file=ref.path,
            line=ref.line,
            title=f"Unresolved reference: {ref.name}",
            description=(
                f"`{ref.name}` is referenced on line {ref.line} but is not "
                f"imported in this file, and {where}. The reference cannot "
                f"resolve as written."
            ),
            evidence=ref.text,
            recommendation=recommendation,
            confidence=1.0,
            is_pre_existing=False,
            hard_block=True,
        ))
    return findings
