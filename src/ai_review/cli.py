"""Command-line interface."""
from __future__ import annotations

import argparse
import os
import sys

from ai_review import __version__
from ai_review.config import load_config_for_repo
from ai_review.hooks import install_hook, uninstall_hook
from ai_review.output import render
from ai_review.pipeline import build_pipeline

EXIT_OK = 0
EXIT_BLOCK = 1
EXIT_ERROR = 2


def _parse_dotted(entries: list[str]) -> dict:
    out: dict = {}
    for entry in entries:
        key, _, value = entry.partition("=")
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _coerce(value)
    return out


def _coerce(value: str) -> "str | int | float | bool":
    """Best-effort scalar coercion for --config values (never raises)."""
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _find_repo_dir() -> str:
    return os.getcwd()


def _print_reviewing_notice(info: dict) -> None:
    """Progress hook: remote reviews can run for minutes — show activity now."""
    print(
        f"ai-review: reviewing {info['files']} file(s) "
        f"(+{info['added']}/-{info['removed']}) with {info['model']} "
        f"via {info['provider']} (timeout {info['timeout_seconds']}s)...",
        file=sys.stderr, flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the ai-review argument parser (see :func:`main` for semantics)."""
    p = argparse.ArgumentParser(prog="ai-review",
                                description="AI Git pre-commit review agent")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--doctor", action="store_true", help="run environment diagnostics")
    p.add_argument("--install-hook", action="store_true", help="install the pre-commit hook")
    p.add_argument("--uninstall-hook", action="store_true", help="remove only our hook integration")
    p.add_argument("--staged", action="store_true", help="review staged changes (default)")
    p.add_argument("--dry-run", action="store_true", help="show steps without calling the LLM")
    p.add_argument("--verbose", action="store_true", help="verbose output")
    p.add_argument("--format", choices=["terminal", "json", "markdown"], default="terminal")
    p.add_argument("--provider", help="override LLM provider")
    p.add_argument("--endpoint", help="override LLM endpoint")
    p.add_argument("--config", action="append", default=[], metavar="KEY=VALUE",
                   help="config override (dotted key), repeatable")
    return p


def main(argv=None) -> int:
    """CLI entry point.

    Exit codes: 0 = PASS / WARN / allow-on-failure, 1 = BLOCK, 2 = error
    (bad flags aside, every handled failure prints ``error: ...`` on stderr
    and returns 2 — a pre-commit consumer must never see a traceback).
    """
    args = build_parser().parse_args(argv)
    repo_dir = _find_repo_dir()

    if args.install_hook:
        try:
            print(install_hook(repo_dir))
        except Exception as exc:
            print(f"error: failed to install hook: {exc}", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK

    if args.uninstall_hook:
        try:
            print(uninstall_hook(repo_dir))
        except Exception as exc:
            print(f"error: failed to uninstall hook: {exc}", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK

    if args.doctor:
        # Lazy import: the doctor module ships separately (Task 16); importing
        # it here keeps module import cost and coupling minimal.
        from ai_review.doctor import run_doctor
        rc, lines = run_doctor(repo_dir)
        print("\n".join(lines))
        return rc

    # Overrides parsing, flag folding and layered load all share one failure
    # contract: ANY malformed --config shape (e.g. scalar-then-dotted conflict
    # "a=1" + "a.b=2") must yield "error: invalid configuration" + exit 2,
    # never an uncaught traceback (which a hook consumer would read as BLOCK).
    try:
        overrides = _parse_dotted(args.config)
        if args.provider:
            overrides.setdefault("llm", {})["provider"] = args.provider
        if args.endpoint:
            overrides.setdefault("llm", {})["endpoint"] = args.endpoint
        cfg = load_config_for_repo(repo_dir, cli_overrides=overrides or None)
    except Exception as exc:
        print(f"error: invalid configuration: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        from ai_review.providers import make_provider
        provider = None if args.dry_run else make_provider(cfg)
        pipe = build_pipeline(repo_dir, cfg, provider=provider,
                              dry_run=args.dry_run, verbose=args.verbose,
                              progress=_print_reviewing_notice)
        result_or_text = pipe.run()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.dry_run:
        print(result_or_text)
        return EXIT_OK

    result = result_or_text
    opts = getattr(pipe, "opts", None)
    meta = getattr(opts, "meta", None) or {}
    text = render(result, fmt=args.format, profile=None, meta=meta)
    print(text)
    if args.verbose and result.issues:
        print(f"\n[verbose] {len(result.issues)} finding(s), policy={result.decision}",
              file=sys.stderr)
    return EXIT_OK if result.decision != "BLOCK" else EXIT_BLOCK
