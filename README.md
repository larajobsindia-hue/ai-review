# ai-review

Technology-agnostic, provider-agnostic AI Git pre-commit code review agent.
Runs entirely against your staged changes before every commit: security scan,
secret redaction, deterministic checks, then an LLM review whose findings are
enforced by a policy engine (block on CRITICAL/HIGH findings).

## Install

```bash
pipx install ai-review          # or: pip install ai-review
# or with uv:
uv tool install ai-review
```

Requires Python >= 3.10.

## Usage

```bash
cd your-repo
ai-review --install-hook        # once per repository
git add . && git commit -m "..."   # hook runs the reviewer automatically
```

To bypass the review for a single commit (never modified by this tool):

```bash
git commit --no-verify
```

## Commands

```
ai-review --staged                review staged changes (default action)
ai-review --dry-run               show the review steps without calling the LLM
ai-review --doctor                probe the environment and report readiness
ai-review --verbose               verbose output
ai-review --format terminal       terminal (default) | json | markdown
ai-review --uninstall-hook        remove only our hook integration
ai-review --version
ai-review --help
```

Exit codes:

| Code | Meaning                                        |
| ---- | ---------------------------------------------- |
| 0    | review passed (clean, or warn-level findings)  |
| 1    | commit blocked (policy-matching findings)      |
| 2    | error (not a git repo, bad config, hook issue) |

`--doctor` probes git, the repository, configuration layers, language
detection, prompt assets, redaction, hook state, and the LLM endpoint. A
missing or unreachable LLM server is reported as a **warning**, not an error.

## Configuration

Precedence (high to low):

1. CLI args (`--provider`, `--endpoint`, `--config key=value`)
2. `.ai-review.yaml` in the repository root
3. `~/.config/ai-review/config.yaml` (user)
4. Organization config — set the `AI_REVIEW_ORG_CONFIG` environment variable
   to a YAML file path; it can enforce mandatory rules (e.g. secret
   redaction that lower layers cannot disable)

See [`examples/.ai-review.example.yaml`](examples/.ai-review.example.yaml) for
a fully commented example.

## Privacy

- **Local mode (default):** repo → agent → local llama.cpp server. No source
  code leaves your machine.
- **Remote mode:** if you configure a remote provider, repository code
  (after secret redaction) is sent to that provider. Review your config
  before enabling.
- Secret scanning is offline and deterministic: hard-coded secrets are
  detected and block the commit even if no LLM is running.
- `git commit --no-verify` is never modified by this tool.

## License

MIT — see [LICENSE](LICENSE).
