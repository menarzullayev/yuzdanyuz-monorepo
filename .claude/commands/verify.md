---
name: verify
description: Run all CI checks locally (mirrors GitHub Actions exactly). Use before push to catch what CI would catch.
---

Run the full local CI mirror — same checks as `.github/workflows/ci.yml` but on your machine.

## Steps

Execute these in order, stop at first failure:

1. **Pre-commit hooks** (lint, format, file checks):
   ```bash
   cd /home/hsm/apps/yuzdanyuz-monorepo
   ./packages/backend/venv/bin/python -m pre_commit run --all-files
   ```

2. **Django system check**:
   ```bash
   cd packages/backend
   ./venv/bin/python manage.py check --fail-level WARNING
   ```

3. **Backend tests** (parallel by category):
   ```bash
   ./venv/bin/python -m pytest tests/unit/ apps/*/tests.py --tb=short -q
   ./venv/bin/python -m pytest tests/integration/ --tb=short -q
   ./venv/bin/python -m pytest tests/security/ --tb=short -q
   ```

4. **Frontend TypeScript** (if frontend changed):
   ```bash
   cd ../frontend-web
   npx tsc --noEmit
   npm run lint
   ```

## Output

After each step, report:
- ✅ Step name (X.Xs)
- ❌ Step name → exact error excerpt + file:line

At end:
```
=== VERIFY SUMMARY ===
✅ All passed (Xs total) — safe to push
   OR
❌ N failed — fix before push
```

## Failure Recovery

If a step fails:
- DO NOT auto-fix
- Show user the exact failure and recommended action
- Suggest specific tests/files to investigate

## Notes

- Run from monorepo root
- PostgreSQL must be running for integration/security tests
- Cache speeds up repeat runs (pytest cache, ruff cache)
