# Review Task

You are reviewing the staged diff below against these generic review criteria:

- Correctness of changed logic and edge cases introduced by the change
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
