# LESSONS.md — Self-Improvement Log

> Patterns learned from mistakes. Read at session start. Add after every user correction.

**Format**: `### Lesson NN — Title` then "Mistake → Why → Fix → Pattern"

---

## Lesson 01 — PostgreSQL: SET app.X = NULL is invalid

**Mistake**: RLSMiddleware used `SET app.current_org_id = NULL;` for anonymous users.

**Why it broke**: PostgreSQL doesn't accept NULL as a session variable value via `SET` (different from `=` in queries). Triggers `syntax error at or near "NULL"`.

**Fix**: Use `RESET app.current_org_id;` to clear, not `SET ... = NULL`.

**Pattern**: When clearing PostgreSQL session variables, always use `RESET <name>`. Reserve `SET <name> = '<value>'` only for actual values.

**Caught by**: pre-push hook (Pytest security tests).

---

## Lesson 02 — Django + django-celery-beat version compatibility

**Mistake**: `requirements/base.txt` pinned `django-celery-beat==2.7.0` while using `Django==5.2.14`. Pip install failed in CI with `ResolutionImpossible`.

**Why**: `django-celery-beat 2.7.0` requires `Django<5.2`. Local venv had it pre-installed so didn't notice.

**Fix**: Bump to `django-celery-beat==2.8.1` (Django 5.2 support).

**Pattern**: Before bumping Django minor version, audit ALL `django-*` dependencies for compatibility. CI's fresh `pip install` catches what local venvs hide.

**Caught by**: GitHub Actions backend job (fresh env).

---

## Lesson 03 — conftest.py imports must be in dev requirements

**Mistake**: `conftest.py` had `import fakeredis` but `fakeredis` was missing from `requirements/dev.txt`. CI failed with `ModuleNotFoundError`.

**Why**: Local venv had it manually installed.

**Fix**: Added `fakeredis==2.35.1` to `requirements/dev.txt`. Same for `pytest-cov`.

**Pattern**: Every import in `conftest.py`, `tests/`, `apps/*/tests.py` MUST be declared in `requirements/dev.txt`. Local venvs lie.

**Caught by**: CI backend job.

---

## Lesson 04 — TypeScript path aliases need both baseUrl + paths

**Mistake**: `tsconfig.json` had `"paths": { "@/*": ["./src/*"] }` but no `"baseUrl": "."`. Imports like `@/services/auth` failed with "Cannot find module".

**Fix**: Added `"baseUrl": "."` alongside `paths`.

**Pattern**: Next.js path aliases need BOTH `baseUrl` (relative reference point) AND `paths` (alias mapping). Either alone is incomplete.

**Caught by**: CI frontend-web job (`tsc --noEmit`).

---

## Lesson 05 — Pre-push hooks catch real bugs

**Mistake**: Hooks felt like overhead, considered `--no-verify` for speed.

**Why we didn't**: Pre-push hook caught Lesson 01 (RLS NULL bug) before it reached CI.

**Pattern**: NEVER `--no-verify` unless explicitly justified. Hooks are slower but they catch bugs in your time, not CI's. The 30-60 sec local cost saves 5-10 min CI cycle + failure shame.

**Caught by**: Lesson 01 retrospective.

---

## Lesson 06 — localhost vs 127.0.0.1 on shared servers

**Mistake**: Set `DB_HOST=localhost` in `.env` on `srvr1.sammu.uz`. Connection failed with "Connection refused" pointing to public IP.

**Why**: On this server, `localhost` resolves to the public IP `109.94.172.117`, not `127.0.0.1`. PostgreSQL listens only on 127.0.0.1.

**Fix**: Use `DB_HOST=127.0.0.1` explicitly.

**Pattern**: On shared/cPanel servers, never trust `localhost` to mean loopback. Always use `127.0.0.1` for local services.

---

## Lesson 07 — VS Code git auto-detection picks up cache repos

**Mistake**: VS Code Source Control panel showed pre-commit framework's hook clones (`~/.cache/pre-commit/repo*/`) as if they were our repos.

**Fix**: `.vscode/settings.json` → `"git.autoRepositoryDetection": "openEditors"` + `"git.detectSubmodules": false`.

**Pattern**: For pre-commit + monorepo workflows, restrict VS Code git detection to `openEditors` to avoid noise from `.cache/` clones.

---

## Lesson 08 — Django model `Meta` must come BEFORE `__str__`

**Mistake**: Added `__str__` before `Meta` class in `QuestionDraft`. Ruff DJ012 caught it.

**Pattern**: Django Style Guide order:
1. Field declarations
2. `Meta` class
3. `__str__` method
4. Other custom methods

---

## Lesson 09 — pre-commit JSON check fails on JSONC

**Mistake**: Added `.vscode/settings.json` with `// comments`. Pre-commit `check-json` rejected it (not valid JSON).

**Fix**: `.pre-commit-config.yaml` → `check-json` exclude `^\.vscode/` (VS Code uses JSONC).

**Pattern**: When configs allow comments (`tsconfig.json`, `.vscode/*.json`), exclude them from strict JSON checkers.

---

## Lesson 10 — Dependabot PRs created from broken main = stale forever

**Mistake**: 17 Dependabot PRs failed CI because main was broken when they were branched. Dependabot doesn't auto-rebase open PRs after main fixes.

**Fix**: Closed all stale PRs. Dependabot recreates next cycle from clean main.

**Pattern**: After fixing main breakage, audit Dependabot PRs. Either close + let recreate, OR comment `@dependabot rebase` on each.

---

## Capture Template

When the user corrects you, append:

```markdown
## Lesson NN — Title

**Mistake**: <what I did>
**Why it broke**: <root cause>
**Fix**: <how I corrected>
**Pattern**: <rule for future>
**Caught by**: <test/hook/user/CI>
```

After 5+ lessons in a category, consider promoting to a hook or skill.
