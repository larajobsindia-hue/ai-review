"""Layered prompt assembly (system + generic + detected techs + diff + context)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ai_review.models import PromptPayload, RepoProfile, StagedChange

OUTPUT_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "decision": {"enum": ["PASS", "BLOCK", "WARN"]},
        "summary": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]},
                    "category": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "evidence": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "is_pre_existing": {"type": "boolean"},
                },
                "required": [
                    "severity", "category", "file", "line", "title",
                    "description", "evidence", "recommendation",
                    "confidence", "is_pre_existing",
                ],
            },
        },
    },
    "required": ["decision", "summary", "issues"],
}

#: Cut-off for the raw diff body rendered by :func:`format_diff` and consumed by
#: ``PromptBuilder.build``; anything beyond this many characters is elided with a
#: ``…[truncated N chars]`` marker.
MAX_DIFF_CHARS = 30_000

#: The prompt assets read by ``PromptBuilder``, ``prompt_version`` and
#: ``prompt_files_ok``; rels are relative to the prompt directory.
_PROMPT_FILES = ["system.md", "review.md", "technology/generic.md"]


def default_prompt_dir() -> str:
    """The shipped prompt asset directory, anchored at this module.

    ``ai_review.prompts`` is a module (``prompts.py``) and the assets live in
    the sibling ``prompts/`` directory inside the installed package — never
    resolve them from the CWD.
    """
    return str(Path(__file__).resolve().parent / "prompts")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _file_line(change: StagedChange) -> str:
    """Render one per-file summary line (matches ``PromptBuilder.build`` format)."""
    line = f"- {change.path} [{change.status}]"
    if change.old_path:
        line += f" (was {change.old_path})"
    if change.is_binary:
        line += " [binary]"
    else:
        line += f" +{change.stat_added}/-{change.stat_removed}"
    return line


def _summary_lines(changes: list[StagedChange], empty: str = "(none)") -> str:
    lines = "\n".join(_file_line(change) for change in changes)
    return lines if lines else empty


def format_diff(changes: list[StagedChange], diff_text: str,
                profile: RepoProfile) -> str:
    """Deterministic, truncated rendering of the staged diff for the prompt.

    Renders a "# Staged Files" per-file summary derived from *changes* (path,
    status, ``+adds/-removes`` stats or ``[binary]``, and ``(was old_path)`` for
    renames), then the raw ``diff_text`` body under "# Staged Diff". When the
    body exceeds the module-level :data:`MAX_DIFF_CHARS` budget it is cut and a
    ``…[truncated N chars]`` marker reports the exact number of characters
    elided. Secret redaction happens upstream (Task 8 ``redact_text``), never
    here. *profile* is accepted for interface coherence with
    :meth:`PromptBuilder.build` so the pipeline can call either, but the
    rendering is derived only from *changes* and *diff_text*.
    """
    summary = _summary_lines(changes)
    body = diff_text
    if len(body) > MAX_DIFF_CHARS:
        removed = len(body) - MAX_DIFF_CHARS
        body = body[:MAX_DIFF_CHARS] + f"\n…[truncated {removed} chars]"
    return f"# Staged Files\n{summary}\n\n# Staged Diff\n{body}"


class PromptBuilder:
    """Assemble the layered prompt payload from the plain-file prompt assets.

    Loads ``system.md`` (fixed system rules), ``review.md`` (generic review
    criteria), and ``technology/generic.md`` (per-technologies template with a
    ``{{technology_summary}}`` placeholder filled from the profile), then lays
    out the user prompt as review rules + technology layer + staged files +
    staged diff + relevant context + (optional) repository rules + the output
    JSON schema. The corrective-retry variant is *not* assembled here: Task 11's
    ``ReviewSession`` composes it by appending a corrective hint to ``system``
    and toggling ``PromptPayload.corrective``.
    """

    def __init__(self, prompt_dir: str):
        self.dir = Path(prompt_dir)

    def _load(self, rel: str) -> str:
        return _read(self.dir / rel)

    def build(self, profile: RepoProfile, changes: list[StagedChange],
              diff_text: str, context_text: str, rules: str = "",
              tech_template: str | None = None) -> PromptPayload:
        """Build a :class:`PromptPayload` for one review.

        *tech_template* overrides the detected-technology layer (defaults to
        ``technology/generic.md``); its ``{{technology_summary}}`` placeholder is
        substituted with the profile table. *rules* renders the
        "# Repository Review Rules" section only when non-blank.
        """
        system = self._load("system.md")
        review = self._load("review.md")

        tech_layer = tech_template or self._load("technology/generic.md")
        table = "\n".join(
            f"- {e.name} (confidence {e.confidence:.2f}) — evidence: "
            + ", ".join(ev.detail for ev in e.evidence[:3])
            for e in profile.languages + profile.frameworks + profile.databases + profile.infrastructure
        ) or "- none detected"
        tech_layer = tech_layer.replace("{{technology_summary}}", table)

        files = _summary_lines(changes, empty="")

        user_parts = [
            review, tech_layer,
            "# Staged Files", files,
            "# Staged Diff", diff_text,
            "# Relevant Context", context_text or "(none supplied)",
        ]
        if rules and rules.strip():
            user_parts += ["# Repository Review Rules", rules.strip()]
        user_parts += [
            "# Output Format",
            "Return ONLY a JSON object matching this schema:",
            f"```json\n{OUTPUT_JSON_SCHEMA}\n```",
        ]
        return PromptPayload(system=system, user="\n\n".join(user_parts),
                             json_schema=OUTPUT_JSON_SCHEMA)


def prompt_version(prompt_dir: str) -> str:
    """Deterministic cache-key input: sha256 hexdigest[:16] of the prompt assets.

    The digest covers the three prompt files (relative path plus content), so a
    content change in any asset invalidates the cache key.

    Raises ``FileNotFoundError`` if an asset is missing; callers must gate on
    :func:`prompt_files_ok` (e.g. ``--doctor``) or guarantee a complete install
    before computing the version.
    """
    digest = hashlib.sha256()
    for rel in _PROMPT_FILES:
        digest.update(rel.encode())
        digest.update((Path(prompt_dir) / rel).read_bytes())
    return digest.hexdigest()[:16]


def prompt_files_ok(prompt_dir: str) -> tuple[bool, list[str]]:
    """Return ``(ok, missing)`` for the prompt assets (used by ``--doctor``)."""
    missing = [rel for rel in _PROMPT_FILES
               if not (Path(prompt_dir) / rel).is_file()]
    return (not missing), missing