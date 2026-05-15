---
name: new-task
description: Start a new feature task — create branch, set up CLAUDE.local.md "currently working on", optionally seed test/model skeleton. Use at the START of new feature work.
---

Start a new task with proper branch + workspace setup.

## Workflow

1. **Get task name** from `$ARGUMENTS` or ask user (kebab-case, e.g., `exam-mock-model`).

2. **Pre-flight**:
   ```bash
   cd /home/hsm/apps/yuzdanyuz-monorepo
   git status
   ```
   - If dirty tree: refuse, ask user to commit/stash first
   - If on main: pull latest before branching

3. **Create branch**:
   ```bash
   git checkout main
   git pull
   git checkout -b feature/<task-name>
   ```

4. **Update CLAUDE.local.md** (personal note):
   - Edit `.claude/CLAUDE.local.md`
   - Section "Currently Working On": replace with `feature/<task-name> — <description>`
   - Add timestamp

5. **Optional skeleton** (ask user):
   - "Want me to scaffold? (model/view/test/migration)"
   - If yes:
     - Create empty test file: `tests/unit/test_<task>.py` (or `apps/X/tests.py` extension)
     - If model task: empty model class in `apps/X/models.py` with `TenantTimestampMixin`
     - If migration task: `python manage.py makemigrations <app> --empty -n <task>`
     - Skeleton includes TODO markers

6. **First commit (empty)** — establishes branch on remote:
   ```bash
   git commit --allow-empty -m "chore: start feature/<task-name>

   Initialize feature branch.

   Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
   git push -u origin feature/<task-name>
   ```

7. **Show next steps**:
   ```
   ✅ Branch: feature/<task-name>
   ✅ Workspace ready
   📝 CLAUDE.local.md updated

   Next:
   - Implement the change
   - When done: run /verify
   - When CI green: run /ship
   ```

## Output

```
🌱 NEW TASK: <task-name>

✅ Branched from main (latest)
✅ CLAUDE.local.md → "Currently Working On"
✅ Skeleton: <skipped|created at X,Y,Z>
✅ Pushed to origin

Next: implement → /verify → /ship
```

## Constraints

- Task name MUST be kebab-case
- Task name should match a roadmap item if possible (check `packages/backend/docs/roadmap.md`)
- Don't create skeleton without asking — assumptions about scope are dangerous
- Refuse if user is mid-conflict or has unstaged work
