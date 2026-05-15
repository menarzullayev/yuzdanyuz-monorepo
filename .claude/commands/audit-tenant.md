---
name: audit-tenant
description: Run a tenant isolation security audit across the codebase. Delegates to tenant-auditor subagent. Use periodically or after auth/middleware changes.
---

Run a comprehensive tenant isolation audit. Delegates the heavy lifting to the `tenant-auditor` subagent.

## Workflow

1. Launch `tenant-auditor` subagent with prompt:
   > Audit the entire codebase for tenant isolation gaps. Focus on:
   > - Models lacking TenantTimestampMixin (justified or risk?)
   > - Raw SQL without tenant filter
   > - global_objects misuse outside admin context
   > - Cross-tenant FK without clean() validation
   > - RLS policy coverage vs models
   > - Middleware order in core/settings/base.py
   > - Boundary test coverage
   >
   > Report under 300 lines. Cite file:line for every finding.
   > Distinguish CRITICAL (live exploit possible) vs WARNING (theoretical risk).

2. Show subagent's report verbatim to user.

3. **If CRITICAL findings exist**: ask user "want me to create issues / fix them?"
   - If issues: `gh issue create` for each
   - If fix: spawn additional Plan/Edit work, NOT auto-applied

## Output

The subagent's structured audit report.

## Notes

- This is read-only delegation
- Don't bypass: this should run on every major auth/middleware change
- Periodic schedule: monthly minimum, weekly during active multi-tenant feature work
