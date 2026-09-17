# Add Static Analysis Layer to ai-review

You are working on the existing `ai-review` project.

Read `AGENTS.md` first and strictly follow its architecture, conventions, test requirements, and invariants.

The existing project is a technology/provider-agnostic AI Git pre-commit review agent.

The current pipeline is approximately:

```text
collect staged
→ detect/profile
→ classify
→ unified diff
→ redact
→ security scan
→ resolution gate
→ prompt build
→ LLM ReviewSession
→ validate findings
→ PolicyEngine
```

Do not break the existing behavior or reorder existing hard-blocking security/resolution gates without a clear reason.

## Objective

Add a new **Static Analysis Layer** that runs deterministic static-analysis tools BEFORE the AI/LLM code review.

The new architecture should be:

```text
Collect staged changes
        ↓
Technology / Framework detection
        ↓
Static Analysis
        ↓
Normalize findings
        ↓
Deduplicate findings
        ↓
Security / Resolution gates
        ↓
Build AI review context
        ↓
LLM Code Review
        ↓
Validate AI findings
        ↓
Merge static + AI findings
        ↓
Policy Engine
```

The exact placement must preserve the existing AGENTS.md invariants. In particular, static analysis must not weaken or bypass the existing security scan or resolution gate.

The purpose of static analysis is to provide deterministic evidence to the AI reviewer.

The AI must not replace static-analysis tools.

---

# 1. Design Goals

The implementation must be:

* modular
* technology-agnostic
* provider-agnostic
* safe
* deterministic where possible
* extensible
* testable offline
* suitable for pre-commit and CI usage

Adding a new static-analysis tool should require implementing a new analyzer module rather than modifying the pipeline extensively.

Do NOT hard-code analyzer logic into `pipeline.py`.

---

# 2. Analyzer Interface

Create a common analyzer abstraction.

Conceptually:

```python
class StaticAnalyzer(Protocol):
    name: str

    def supports(self, context: AnalysisContext) -> bool:
        ...

    def is_available(self) -> bool:
        ...

    def analyze(
        self,
        context: AnalysisContext,
    ) -> AnalyzerResult:
        ...
```

Adapt the exact design to the existing project's conventions.

The analyzer should receive information such as:

```text
repository root
changed/staged files
detected languages
detected frameworks
project profile
git diff
configuration
```

---

# 3. Initial Analyzers

Implement support for these analyzers.

## Universal

### Semgrep

Use when available.

Prefer machine-readable JSON output.

### SonarQube

Treat SonarQube as optional.

Do not require a running SonarQube server.

Only run it when explicitly configured and available.

### CodeQL

Treat CodeQL as optional.

Do not make it a mandatory dependency.

Only run when explicitly configured and available.

---

# 4. PHP

Support:

```text
PHPStan
Larastan
PHP_CodeSniffer
```

Laravel detection should use the existing project technology detection where possible.

If Laravel is detected, Larastan may be enabled.

Do not assume all PHP projects are Laravel.

---

# 5. JavaScript / TypeScript

Support:

```text
ESLint
TypeScript compiler
```

Use:

```text
eslint --format json
```

or the appropriate structured output.

For TypeScript, use type checking without unnecessarily executing application code.

Respect an existing:

```text
tsconfig.json
```

when present.

---

# 6. Python

Support:

```text
Ruff
mypy
Pylint
```

Prefer structured output.

Ruff should be the primary lightweight analyzer when available.

---

# 7. Go

Support:

```text
go vet
staticcheck
golangci-lint
```

Prefer:

```text
go vet
staticcheck
```

for the initial implementation.

Do not require all Go analyzers.

---

# 8. Java

Support:

```text
Checkstyle
PMD
SpotBugs
```

These should be optional and activated only when the corresponding project/tool configuration is available.

---

# 9. C / C++

Support:

```text
clang-tidy
cppcheck
```

---

# 10. Ruby

Support:

```text
RuboCop
Brakeman
```

Brakeman should only run when Rails is detected.

---

# 11. SQL

Support:

```text
SQLFluff
```

Only analyze SQL files or SQL content that can be safely identified.

---

# 12. Docker / Containers

Support:

```text
Hadolint
Trivy
```

Hadolint should analyze Dockerfiles.

Trivy should be configurable for filesystem/container/IaC scanning.

Do not unexpectedly download large databases during a normal pre-commit review.

---

# 13. Terraform

Support:

```text
TFLint
Checkov
```

---

# 14. Kubernetes

Support:

```text
kubeconform
kube-linter
```

---

# 15. Tool Availability

No analyzer should be a mandatory installation requirement.

For every analyzer:

```text
detect executable
→ verify version if possible
→ run only when available
```

If unavailable, return a structured status:

```json
{
  "tool": "phpstan",
  "status": "unavailable"
}
```

Do NOT fail the complete review simply because an optional analyzer is missing.

---

# 16. Safe Tool Execution

Create or reuse a common subprocess runner.

Requirements:

* no unsafe shell interpolation
* argument arrays instead of shell strings
* configurable timeout
* stdout capture
* stderr capture
* exit-code capture
* controlled environment
* cancellation support if practical
* repository path must be safely handled

Do not execute arbitrary project scripts simply because they exist in the repository.

Static analysis must not unexpectedly run:

```text
npm scripts
composer scripts
Makefiles
shell scripts
application startup commands
```

unless an analyzer explicitly requires it and that behavior is documented/configured.

---

# 17. Changed Files

The default mode should focus on staged/changed files where the analyzer supports it.

However, some analyzers need project-wide context.

Therefore each analyzer should declare something like:

```text
scope = changed_files
```

or:

```text
scope = project
```

Examples:

```text
ESLint → changed files
PHPStan → project/context aware
Larastan → project/context aware
Semgrep → changed files where practical
go vet → package/project context
CodeQL → project
```

Do not force every analyzer into changed-file mode if that makes the analysis incorrect.

---

# 18. Common Finding Model

Create a normalized internal finding model.

For example:

```json
{
  "id": "stable-id",
  "source": "static_analysis",
  "tool": "semgrep",
  "rule_id": "php.security.sql-injection",
  "category": "security",
  "severity": "high",
  "confidence": "high",
  "message": "Potential SQL injection.",
  "description": "...",
  "file": "app/Models/User.php",
  "line": 42,
  "column": 10,
  "end_line": 42,
  "end_column": 50,
  "code": "...",
  "fingerprint": "...",
  "cwe": [],
  "owasp": [],
  "documentation_url": null
}
```

Adapt this to the existing finding schema if one already exists.

Do NOT create a competing finding model if the project already has an appropriate model.

Extend existing models where appropriate.

---

# 19. Preserve Original Analyzer Information

The normalized finding must retain:

```text
tool
rule_id
original severity
original message
original location
```

Do not lose the analyzer's evidence.

Example:

```json
{
  "severity": "high",
  "original_severity": "ERROR",
  "tool": "phpstan"
}
```

---

# 20. Severity Normalization

Normalize analyzer severities into:

```text
critical
high
medium
low
info
```

But preserve the original severity.

Each analyzer should define an explicit mapping.

Do not assume that:

```text
ERROR = critical
```

for every tool.

---

# 21. Confidence

Keep severity and confidence separate.

For example:

```text
severity: high
confidence: medium
```

Use:

```text
high
medium
low
```

for confidence.

---

# 22. Deduplication

Multiple tools may report the same underlying problem.

Implement deterministic deduplication.

Potential matching information:

```text
file
line
rule
category
normalized message
code fingerprint
CWE
```

Do not blindly merge findings that are merely near each other.

When findings are merged, preserve the tools that detected them:

```json
{
  "detected_by": [
    "semgrep",
    "sonarqube",
    "codeql"
  ]
}
```

---

# 23. Static Analysis Summary

Create an analysis summary:

```json
{
  "tools_run": 6,
  "tools_available": 7,
  "tools_unavailable": 2,
  "files_analyzed": 42,
  "findings": 17,
  "critical": 0,
  "high": 3,
  "medium": 8,
  "low": 6,
  "duration_ms": 12340
}
```

The exact schema should follow existing project conventions.

---

# 24. Feed Static Findings to the AI

This is the most important part.

The LLM prompt must contain static-analysis results.

The AI should receive:

```text
Git diff
+
Relevant source context
+
Technology/framework information
+
Static-analysis findings
+
Existing security findings
+
Existing resolution findings
```

Do not simply append an enormous raw analyzer output.

Create a compact AI-oriented representation.

Example:

```text
STATIC ANALYSIS FINDINGS

[HIGH] PHPStan
File: app/Services/OrderService.php:142
Rule: argument.type
Message: ...

[MEDIUM] Semgrep
File: app/Http/Controllers/UserController.php:57
Rule: ...
Message: ...
```

Only include relevant findings when possible.

---

# 25. AI's Responsibility

The AI code reviewer should use static-analysis findings as evidence.

The AI should determine:

1. Is the finding valid?
2. Is it actually reachable?
3. Is it a false positive?
4. Does surrounding code mitigate the issue?
5. What is the actual impact?
6. Is there a better fix?
7. Are there related problems?
8. Does the change introduce an architectural problem?
9. Does the finding matter to the actual application context?

The AI must NOT blindly repeat static-analysis findings.

---

# 26. Separate Static Findings From AI Findings

Maintain provenance.

For example:

```json
{
  "source": "static_analysis"
}
```

versus:

```json
{
  "source": "ai"
}
```

If the AI confirms a static finding, represent that relationship explicitly.

Example:

```json
{
  "source": "ai",
  "related_static_finding_id": "abc123",
  "verdict": "confirmed"
}
```

This allows the final UI to say:

```text
Detected by Semgrep
Confirmed by AI review
```

rather than making it appear that the AI discovered the issue independently.

---

# 27. False Positives

The AI should be able to classify static findings as:

```text
confirmed
likely_true
uncertain
likely_false_positive
false_positive
```

Keep the original static finding unchanged.

Store the AI assessment separately.

---

# 28. Configuration

Extend the existing `.ai-review.yaml` configuration.

Example:

```yaml
static_analysis:
  enabled: true

  fail_on_error: false

  timeout: 120

  tools:
    semgrep:
      enabled: true

    phpstan:
      enabled: true

    larastan:
      enabled: true

    eslint:
      enabled: true

    ruff:
      enabled: true

    staticcheck:
      enabled: true

    codeql:
      enabled: false

    sonarqube:
      enabled: false
```

Follow the existing configuration layering rules from AGENTS.md.

Do not bypass organization-level configuration enforcement.

---

# 29. CLI

Add useful commands/options without breaking the current CLI contract.

Examples:

```bash
ai-review analyze --static-only
```

```bash
ai-review analyze --no-static-analysis
```

```bash
ai-review doctor
```

The doctor output should show:

```text
Static Analysis

Semgrep        ✓
PHPStan        ✓
Larastan       ✓
ESLint         ✓
Ruff           ✓
Staticcheck    ✗ not installed
CodeQL         ✗ not configured
Trivy          ✓
```

Reuse the existing `--doctor` behavior rather than creating conflicting semantics.

---

# 30. JSON / SARIF

Support machine-readable output.

At minimum:

```text
analysis.json
```

Prefer SARIF support because it is a common format for static-analysis/security findings.

Architecture:

```text
Tool output
     ↓
Parser
     ↓
Normalized Finding
     ↓
JSON / SARIF / AI context
```

The normalized model must remain independent of SARIF.

---

# 31. Performance

Run independent analyzers concurrently where safe.

Use configurable concurrency.

Do not create hundreds of processes on a developer workstation.

Respect analyzer dependencies and project context.

A fast pre-commit review should be prioritized.

Allow expensive tools such as CodeQL/SonarQube to be CI-only or explicitly enabled.

---

# 32. Offline Testing

The entire test suite must remain fully offline.

Do NOT require:

```text
LLM server
Internet
SonarQube server
CodeQL database download
external API
```

for normal unit tests.

Mock subprocess execution.

Create fixture outputs for:

```text
Semgrep
PHPStan
ESLint
Ruff
Staticcheck
etc.
```

Test parsers independently from actual tool execution.

---

# 33. Tests

Add tests for:

### Analyzer registry

* registration
* lookup
* supported technology
* disabled analyzer

### Tool detection

* installed
* missing
* invalid version

### Execution

* successful tool
* non-zero exit
* timeout
* malformed output
* empty output

### Parsing

* JSON
* SARIF
* tool-specific output

### Normalization

* severity
* confidence
* locations
* fingerprints

### Deduplication

* exact duplicates
* near duplicates
* independent findings

### Technology detection

Test:

```text
Laravel
PHP
Node
TypeScript
Python
Go
Java
Ruby
Docker
Terraform
Kubernetes
```

### Pipeline

Verify:

```text
static analysis
→ AI review
```

and verify that static findings are present in the LLM input.

Use existing:

```text
StaticProvider
RecordingProvider
DeadProvider
```

where appropriate.

Do not require a live LLM endpoint.

---

# 34. Backward Compatibility

The existing behavior must continue to work.

If static analysis is disabled:

```text
existing pipeline behavior
```

should remain functional.

If an analyzer is unavailable:

```text
AI review should still run
```

unless the user explicitly configured that analyzer as mandatory.

Do not change existing exit-code semantics:

```text
0 = pass/warn
1 = blocked
2 = error
```

Do not allow an optional analyzer failure to become an unexpected CLI error.

---

# 35. Important Policy Decision

Static analysis findings should NOT automatically become blocking findings simply because a tool reports them.

The existing policy engine should remain responsible for blocking decisions.

The architecture should distinguish:

```text
Static analyzer detected something
```

from:

```text
Policy says this should block
```

The AI may provide additional evidence, but the final blocking decision must continue through the existing deterministic validation/policy architecture.

---

# 36. Suggested Internal Structure

Adapt this to the existing source layout:

```text
src/ai_review/
    static_analysis/
        __init__.py
        base.py
        context.py
        registry.py
        runner.py
        models.py
        normalizer.py
        deduplicator.py
        doctor.py

        analyzers/
            semgrep.py
            sonarqube.py
            codeql.py
            phpstan.py
            larastan.py
            phpcs.py
            eslint.py
            typescript.py
            ruff.py
            mypy.py
            staticcheck.py
            govet.py
            checkstyle.py
            pmd.py
            spotbugs.py
            clang_tidy.py
            cppcheck.py
            rubocop.py
            brakeman.py
            sqlfluff.py
            hadolint.py
            trivy.py
            tflint.py
            checkov.py
            kubeconform.py
            kube_linter.py
```

Do not blindly create all modules if the project architecture suggests a better organization.

Start with the highest-value analyzers and make the framework extensible.

---

# 37. Recommended Initial Implementation Priority

Implement in this order:

### Phase 1

```text
Semgrep
PHPStan
ESLint
Ruff
Staticcheck
```

### Phase 2

```text
Larastan
TypeScript
go vet
SQLFluff
Hadolint
Trivy
```

### Phase 3

```text
CodeQL
SonarQube
Checkov
TFLint
kubeconform
kube-linter
```

This prevents the initial implementation from becoming unnecessarily large.

---

# 38. AI Prompt Design

Modify the AI review prompt so static-analysis evidence is clearly separated.

Use a structure similar to:

```text
## Repository Context

...

## Technology

...

## Changes

...

## Static Analysis Findings

...

## Security Findings

...

## Resolution Findings

...

## Review Instructions

Review the code independently.

Use static-analysis findings as evidence, not as unquestionable truth.

For each relevant finding:

- verify it against the source
- determine whether it is a real issue
- determine impact
- identify false positives
- explain why
- recommend a concrete fix

Also identify issues that static-analysis tools cannot detect, including:

- business logic errors
- architectural problems
- incorrect assumptions
- API contract problems
- concurrency issues
- maintainability problems
- missing validation
- incorrect error handling
```

This makes the LLM complementary to static analysis instead of redundant.

---

# 39. Final Review Result

The final result should clearly distinguish:

```text
Static Analysis
    ↓
deterministic findings

AI Review
    ↓
contextual findings

Validation
    ↓
validated findings

Policy
    ↓
pass / warn / block
```

Example:

```text
Review Summary

Static Analysis:
  7 findings
  1 high
  4 medium
  2 low

AI Review:
  3 confirmed
  2 false positives
  1 additional architectural issue

Final:
  4 actionable findings
```

---

# 40. Implementation Process

Before changing code:

1. Inspect the existing repository.
2. Read `AGENTS.md`.
3. Inspect `pipeline.py`.
4. Inspect the existing finding models.
5. Inspect security scanning.
6. Inspect resolution handling.
7. Inspect prompt construction.
8. Inspect `ReviewSession`.
9. Inspect `validate_findings`.
10. Inspect `PolicyEngine`.
11. Inspect configuration.
12. Inspect existing tests.

Then produce a concise implementation plan.

After approval/plan, implement incrementally.

Do not rewrite unrelated components.

After implementation run:

```bash
.venv/bin/python -m pytest
```

The existing test suite must remain green.

Also add new unit/integration tests for the static-analysis layer.

Run:

```bash
.venv/bin/ai-review --doctor
```

and:

```bash
.venv/bin/ai-review --dry-run
```

to verify the existing CLI behavior remains intact.

Finally report:

```text
Architecture changes
Files added
Files modified
Analyzers implemented
Configuration changes
CLI changes
Tests added
Test results
Known limitations
Next recommended phase
```

