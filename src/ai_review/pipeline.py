"""Functional pipeline: the composition root wiring every shipped module.

Run sequence: collect staged -> detect (scoped profile via ``profile_from_names``)
-> classify -> static analysis (enabled analyzers; evidence only, never a gate)
-> diff text -> redact -> context placeholder -> scan_staged
security findings -> resolution gate (missing imports / unresolved references,
provider-independent) -> [dry-run short-circuit: provider never touched] ->
build prompt (assets anchored inside the shipped ``ai_review.prompts`` package)
-> ``ReviewSession.run`` -> merge security + resolution findings (prepend) ->
``validate_findings`` -> ``PolicyEngine.decide``.

Type duality: :meth:`Pipeline.run` returns a :class:`~ai_review.models.ReviewResult`
normally, but the dry-run plan text (``str``) when the pipeline was built with
``dry_run=True`` — on that path the provider is never invoked. The separate
:meth:`Pipeline.dry_run` method returns that same plan text without the flag.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from ai_review import __version__
from ai_review.classifier import classify
from ai_review.config import AppConfig, load_config_for_repo
from ai_review.detector import detect
from ai_review.diff import attach_hunks, parse_unified_diff
from ai_review.git import collect_staged, git_branch, git_diff_text, git_repo_root
from ai_review.models import ReviewResult
from ai_review.parser import ParseError
from ai_review.policy import FailureDecision, PolicyEngine
from ai_review.profile import profile_from_names
from ai_review.prompts import PromptBuilder, default_prompt_dir
from ai_review.providers.base import LlamaServerNotFound, ProviderError
from ai_review.resolution import scan_unresolved
from ai_review.reviewer import ReviewSession
from ai_review.security import redact_text, scan_staged
from ai_review.static_analysis import (build_context, format_static_findings,
                                       plan_static_analysis, run_static_analysis)
from ai_review.static_analysis.registry import NOT_APPLICABLE
from ai_review.validator import validate_findings

__all__ = ["Pipeline", "RunnerOptions", "build_pipeline"]


@dataclass
class RunnerOptions:
    repo_dir: str
    cfg: AppConfig
    provider: object
    dry_run: bool = False
    #: Offline-only mode: run the offline stages (static + security + resolution)
    #: and report them, never invoking the provider.
    static_only: bool = False
    verbose: bool = False
    select_only: object = None  # Phase 4: partial-review selection hook
    progress: object = None  # callable(dict) fired just before the LLM call
    meta: dict = field(default_factory=dict)


def build_pipeline(
    repo_dir: str,
    cfg: AppConfig | None = None,
    *,
    provider: object | None = None,
    dry_run: bool = False,
    static_only: bool = False,
    verbose: bool = False,
    select_only: object | None = None,
    progress: object | None = None,
) -> Pipeline:
    """Compose a :class:`Pipeline`; *cfg* defaults to the layered repo config.

    *progress* is an optional ``callable(dict)`` invoked once just before the
    LLM call with ``{"repo", "files", "added", "removed", "provider",
    "model", "timeout_seconds"}`` — remote reviews can run for minutes, so
    callers (the CLI) use it to surface immediate feedback on stderr.
    """
    cfg = cfg or load_config_for_repo(repo_dir)
    return Pipeline(RunnerOptions(
        repo_dir=repo_dir, cfg=cfg, provider=provider, dry_run=dry_run,
        static_only=static_only, verbose=verbose, select_only=select_only,
        progress=progress,
    ))


class Pipeline:
    """One review execution over the staged changes of a repository."""

    def __init__(self, opts: RunnerOptions):
        self.opts = opts
        self.session_factory = lambda: ReviewSession(opts.provider)
        #: The ReviewSession used by the most recent review call (test hook).
        self.last_session: ReviewSession | None = None

    def commit_meta(self, repo_dir: str) -> dict:
        """Base metadata: repository basename and current branch."""
        root = git_repo_root(repo_dir)
        return {"repo": os.path.basename(root), "branch": git_branch(root)}

    def collect(self) -> tuple[str, list]:
        """Collect staged changes and attach parsed hunks; returns (root, changes)."""
        root = git_repo_root(self.opts.repo_dir)
        changes = collect_staged(root, ignore=())
        attach_hunks(changes, parse_unified_diff(self._diff_text(root)))
        return root, changes

    def select_only(self, name: str):
        """Phase 4 hook: select a partial review target. Returns None until then."""
        return None

    def run(self) -> "ReviewResult | str":
        """Execute the full review.

        Returns a :class:`~ai_review.models.ReviewResult` normally; the dry-run
        plan text (``str``) when built with ``dry_run=True`` (provider untouched).
        With ``static_only=True`` the offline stages still run and are reported,
        and the provider is never constructed or called.
        """
        # --dry-run plans the analyzers and executes none of them: the plan text
        # promises "no external calls", so it must not spawn a tool either.
        (meta, secret_kinds, security_findings, resolution_findings, profile,
         changes, redacted, static_summary) = self._prepare(
            plan_only=self.opts.dry_run)

        if self.opts.dry_run:
            self.opts.meta = meta
            return self._dry_run_report(meta, secret_kinds, static_summary)

        cfg = self.opts.cfg
        started = time.monotonic()
        failed = False
        message = ""
        if self.opts.static_only:
            # Offline-only report: the offline stages above are the whole review,
            # and the summary says so honestly instead of claiming an AI verdict.
            result = ReviewResult(
                decision="PASS",
                summary=self._static_only_summary(static_summary),
                static=static_summary,
            )
        else:
            if self.opts.progress is not None:
                # Remote reviews can run for minutes; give the caller a hook to
                # surface immediate feedback (the CLI writes it to stderr).
                self.opts.progress({
                    "repo": meta["repo"], "files": meta["files"],
                    "added": meta["added"], "removed": meta["removed"],
                    "provider": cfg.llm.provider, "model": cfg.llm.model,
                    "timeout_seconds": cfg.llm.timeout_seconds,
                })
            try:
                result = self._review(profile, changes, redacted, static_summary)
            except (LlamaServerNotFound, ProviderError, ParseError) as exc:
                # ReviewUnavailable (a ParseError subclass) lands here too.
                failed = True
                message = str(exc)
                result = FailureDecision().apply(
                    cfg.failure_policy.on_llm_unavailable, message)
            # Attach the evidence before validation: validate_findings resolves
            # the AI's related_static_finding_id links against it.
            result.static = static_summary

        result.issues = security_findings + resolution_findings + result.issues
        result = validate_findings(result, changes)
        gated = PolicyEngine(cfg.policy).decide(result)

        if self.opts.static_only and gated.decision != "BLOCK":
            # PolicyEngine.decide() derives its verdict and summary from the
            # validated issues only and would rewrite this to
            # "PASS / No issues found." — claiming an AI review that never ran.
            # The offline report therefore stands unless the gate BLOCKs on a
            # real offline finding (a staged secret or an unresolved reference).
            gated.decision = "PASS"
            gated.summary = self._static_only_summary(static_summary)

        if failed and gated.decision != "BLOCK":
            # PolicyEngine.decide() derives its verdict from the validated
            # issues only and would rewrite a skipped review to
            # "PASS / No issues found.", contradicting FailureDecision's
            # contract (never claim the AI review passed). The failure_policy
            # outcome therefore stands — unless the gate BLOCKs on real,
            # validated findings: offline + a staged secret (or an unresolved
            # reference) still blocks via hard_block. NOTE: validate_findings also transiently downgrades
            # a failure-BLOCK to WARN (no CRITICAL/HIGH findings); this rescue
            # is what restores it — do not reorder run() without preserving it.
            outcome = FailureDecision().apply(
                cfg.failure_policy.on_llm_unavailable, message)
            outcome.issues = gated.issues
            gated = outcome

        meta["duration_s"] = round(time.monotonic() - started, 1)
        meta["checks"] = gated.checks
        self.opts.meta = meta
        return gated

    def dry_run(self) -> str:
        """The same plan text as ``run()`` with ``dry_run=True``, without the flag."""
        meta, secret_kinds, _, _, _, _, _, static_summary = self._prepare(plan_only=True)
        self.opts.meta = meta
        return self._dry_run_report(meta, secret_kinds, static_summary)

    # -- internals ---------------------------------------------------------

    def _prepare(self, *, plan_only: bool = False):
        """Run every offline stage; the provider is never touched here.

        Returns ``(meta, secret_kinds, security_findings, resolution_findings,
        profile, changes, redacted_diff, static_summary)``. *plan_only* (used by
        the dry-run paths) lists the static analyzers a real run would use
        without executing any of them.
        """
        self._last_diff_text = None  # per-run cache; collect() fetches once
        root, changes = self.collect()
        cfg = self.opts.cfg
        names = [c.path for c in changes]
        profile = profile_from_names(detect(names, root), names)
        # Review depth (Phase 1: computed per the wired sequence; consumed by
        # later phases when depth-aware chunking lands).
        _classes = classify(names, cfg.generated.ignore)
        diff_text = self._diff_text(root)
        if cfg.security.redact_secrets:
            redacted, secret_kinds = redact_text(diff_text)
        else:
            redacted, secret_kinds = diff_text, []
        security_findings = scan_staged(root, changes, cfg.security.excluded_files)
        # Unresolved references (a missing import) are decided offline and
        # hard-block: the provider's verdict must never be able to pass code
        # that cannot resolve as written.
        resolution_findings = scan_unresolved(
            root, changes, ignore=cfg.generated.ignore,
            allowlist=cfg.resolution.allowlist, severity=cfg.resolution.severity,
        ) if cfg.resolution.enabled else []

        # Deterministic tool evidence (design D1/D5): runs after detection and
        # before the hard gates, and its findings never enter result.issues —
        # the policy engine still decides blocking from validated AI findings
        # plus these offline gates only.
        static_summary = None
        if cfg.static_analysis.enabled:
            static_ctx = build_context(root, cfg, changes, profile, diff_text)
            static_summary = (
                plan_static_analysis(cfg.static_analysis, static_ctx) if plan_only
                else run_static_analysis(cfg.static_analysis, static_ctx)
            )

        meta = self.commit_meta(root)
        meta.update({
            "files": len(changes),
            "added": sum(c.stat_added for c in changes),
            "removed": sum(c.stat_removed for c in changes),
            "detected": profile.all_names(),
            "checks": [],
            "duration_s": None,
        })
        self._last_diff_text = diff_text
        return (meta, secret_kinds, security_findings, resolution_findings,
                profile, changes, redacted, static_summary)

    def _diff_text(self, root: str) -> str:
        """The staged unified diff, fetched once per run and reused."""
        if getattr(self, "_last_diff_text", None) is None:
            self._last_diff_text = git_diff_text(root)
        return self._last_diff_text

    def _review(self, profile, changes, redacted, static_summary=None):
        builder = PromptBuilder(self._prompt_dir())
        payload = builder.build(
            profile=profile, changes=changes, diff_text=redacted,
            context_text=self._context_text(changes),
            max_diff_chars=self.opts.cfg.review.max_diff_kb * 1024,
            static_text=format_static_findings(static_summary),
        )
        session = self.session_factory()
        self.last_session = session
        return session.run(payload)

    @staticmethod
    def _static_only_summary(static_summary) -> str:
        """Honest ``--static-only`` prose: no AI verdict was ever produced."""
        findings = getattr(static_summary, "findings", None) or []
        tools = getattr(static_summary, "tools", None) or []
        ran = [tool.name for tool in tools if tool.status == "run"]
        return (f"AI review was skipped (--static-only). Static analysis: "
                f"{len(findings)} finding(s) from {len(ran)} tool(s) "
                f"({', '.join(ran) or 'none ran'}).")

    def _context_text(self, changes) -> str:
        return ""  # Phase 2: context engine

    def _prompt_dir(self) -> str:
        return default_prompt_dir()

    def _dry_run_report(self, meta, secret_kinds, static_summary=None) -> str:
        if self.opts.cfg.security.redact_secrets:
            redaction = "matched: " + (", ".join(secret_kinds) or "none")
        else:
            redaction = "disabled"
        tools = getattr(static_summary, "tools", None) or []
        # "Planned" is what a real run would execute. An enabled analyzer that
        # does not match this change set (wrong language, no matching file) is
        # reported separately rather than promised as work that will happen.
        planned = [t.name for t in tools if t.error != NOT_APPLICABLE]
        inapplicable = [t.name for t in tools if t.error == NOT_APPLICABLE]
        planned_text = ", ".join(planned) or "none"
        if inapplicable:
            planned_text += " — skipped: " + ", ".join(inapplicable)
        lines = [
            f"ai-review v{__version__} dry-run",
            f"repo: {meta['repo']}  branch: {meta['branch']}",
            f"changes: {meta['files']} files  +{meta['added']} -{meta['removed']}",
            "detected: " + (", ".join(meta["detected"]) or "none"),
            "steps:",
            "  1. git diff --cached (collect staged)",
            "  2. detect technologies -> profile",
            "  3. classify files -> review depth",
            f"  4. static analysis (planned: {planned_text})  [NOT run in dry-run]",
            f"  5. redact secrets ({redaction})",
            "  6. secret scan",
            "  7. resolution gate (unresolved references / missing imports)",
            "  8. build layered prompt",
            "  9. call LLM via configured provider  [SKIPPED in dry-run]",
            " 10. parse + validate findings",
            " 11. policy decision",
        ]
        return "\n".join(lines)
