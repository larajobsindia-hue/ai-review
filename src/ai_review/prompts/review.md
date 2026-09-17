# Review Task

You are reviewing the staged diff below against these generic review criteria:

- Correctness of changed logic and edge cases introduced by the change
- Reference resolution: every class, function, constant, and module named on
  an added line must be imported into that file (`use` / `import` / `require`)
  or defined in the file or an enclosing scope visible in the reviewed content
- Configuration and wiring added by the change (routes, bindings, registrations)
  must point at existing, resolvable targets
- Security: injection, secrets, authz, unsafe deserialization
- Error handling: exceptions/error paths introduced by the change
- Performance and resource handling in the changed code
- Concurrency and data integrity affected by the change
- API / backward compatibility impact of the change
- Maintainability and testability of new code (do not block for style)

Rules for findings:
- Only report problems introduced, exposed, or worsened by these changes.
- A pre-existing problem must be marked is_pre_existing=true and may be INFO only.
- Do not block for speculative "could theoretically fail" claims.
- For each issue give concrete evidence from the diff and a recommendation.
- confidence must be between 0 and 1. Lower confidence if evidence is weak.
- Do not assume a symbol resolves because the project probably defines it
  somewhere else. The diff and the relevant context are your only evidence: if
  an added line calls a symbol and no matching import or definition appears in
  the reviewed content, report it as BUG and name the missing import — never
  guess that the import exists outside the diff.
- A missing import (or wrong namespace) for a line this change added is
  introduced by the change, so is_pre_existing must be false even though the
  absent import line itself is outside the diff.
