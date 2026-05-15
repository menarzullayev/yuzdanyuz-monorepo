---
name: ship
description: Full ship pipeline — verify + commit + push + (PR if feature branch). One command from "code done" to "PR open".
---

Take work from "I think it's done" to "PR open and CI green". Stop at first failure.

## Workflow

1. **Sanity check current state**:
   ```bash
   cd /home/hsm/apps/yuzdanyuz-monorepo
   git status
   git branch --show-current
   ```
   - If on `main`: STOP, refuse to ship from main. Suggest creating feature branch.
   - If clean tree: ask "nothing to ship — did you save?"

2. **Run /verify** (lint + tests + frontend) — block on failure.

3. **Stage changes intelligently**:
   ```bash
   git status --short
   ```
   - Show user what will be staged
   - `git add` only relevant files (NEVER `git add .` blindly)
   - Skip: tsbuildinfo, log files, cache files (should be gitignored, but verify)

4. **Generate commit message**:
   - Read `git diff --cached`
   - Identify primary change type (feat/fix/chore/docs/etc.)
   - Identify scope (auth/catalog/exams/etc.)
   - Write 1-line summary + body explaining WHY
   - Format: conventional commits (see `~/.claude/skills/git-workflow/SKILL.md`)
   - Add `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`
   - **Show user the message before committing** — let them edit if needed

5. **Commit** (uses HEREDOC for multi-line):
   ```bash
   git commit -m "$(cat <<'EOF'
   <type(scope): summary>

   <body>

   Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
   EOF
   )"
   ```

6. **Push** (triggers pre-push hook, which re-runs critical tests):
   ```bash
   git push -u origin <branch>
   ```

7. **PR creation** (if feature branch):
   - Check if PR already exists: `gh pr view 2>/dev/null`
   - If not: create one
     ```bash
     gh pr create --title "<commit title>" --body "$(cat <<'EOF'
     ## Summary
     <bullet points from commit>

     ## Test plan
     - [ ] CI passes
     - [ ] Manual verification: <specific steps>

     🤖 Generated with [Claude Code](https://claude.com/claude-code)
     EOF
     )"
     ```
   - Output PR URL

8. **Watch CI** (optional, ask user):
   ```bash
   gh run watch $(gh run list --branch <branch> --limit 1 --json databaseId -q '.[0].databaseId')
   ```

## Output

```
🚢 SHIP PIPELINE

✅ Branch: feature/X (not main)
✅ /verify: all 5 stages green (Xs)
✅ Staged: 7 files
📝 Commit message preview:
   feat(exams): add MockExam model with browser lock
   ...

[user confirms or edits]

✅ Commit: a1b2c3d
✅ Push: origin/feature/X
✅ PR: #42 https://github.com/.../pull/42

CI running... [show URL or "watching..."]
```

## Failure Recovery

- /verify fails → stop, show exact failure
- Pre-push hook fails → stop, show failure (do NOT --no-verify)
- PR creation fails → commit/push still done, retry PR step manually

## Notes

- This is `git add + commit + push + PR` automated, BUT with quality gates
- Never bypass hooks
- Always show commit message preview for user review
- Match user's language for commit body if they're working in non-English
