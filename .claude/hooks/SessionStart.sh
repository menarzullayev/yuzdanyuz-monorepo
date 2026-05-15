#!/bin/bash
# SessionStart hook — fires when Claude Code session starts
# Output is shown to Claude as additional context.

set -euo pipefail
cd "$(dirname "$0")/../.." || exit 0

# Bo'sh output → no context. Output → injected as system context.

cat <<EOF
## 📊 Project Status (auto-loaded by SessionStart)

EOF

# ── Git status ────────────────────────────────────────────────────
echo "### Git"
BRANCH=$(git branch --show-current 2>/dev/null || echo "?")
LAST=$(git log --oneline -1 2>/dev/null || echo "(no commits)")
DIRTY=$(git status --porcelain 2>/dev/null | wc -l)

echo "- Branch: \`$BRANCH\`"
echo "- Last commit: $LAST"
echo "- Working tree: $DIRTY uncommitted change(s)"

# Behind/ahead remote
if git rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1; then
  AHEAD=$(git rev-list --count @{u}..HEAD 2>/dev/null || echo "0")
  BEHIND=$(git rev-list --count HEAD..@{u} 2>/dev/null || echo "0")
  if [ "$AHEAD" != "0" ] || [ "$BEHIND" != "0" ]; then
    echo "- vs origin: ↑$AHEAD ↓$BEHIND"
  fi
fi

# ── CI status (gh, if available) ──────────────────────────────────
echo ""
echo "### CI"
if command -v gh >/dev/null 2>&1; then
  CI=$(gh run list --branch "$BRANCH" --limit 1 --json status,conclusion,workflowName 2>/dev/null | \
       python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    if not d:
        print('(no recent runs)')
    else:
        r=d[0]
        c=r.get('conclusion') or r.get('status')
        icon={'success':'✅','failure':'❌','in_progress':'⏳'}.get(c,'❓')
        print(f\"{icon} {r.get('workflowName','?')}: {c}\")
except: print('(unavailable)')
" 2>/dev/null || echo "(gh error)")
  echo "- $CI"
else
  echo "- (gh CLI not installed)"
fi

# ── Recent LESSONS (top 3) ────────────────────────────────────────
if [ -f .claude/LESSONS.md ]; then
  echo ""
  echo "### Recent Lessons (top 3, see .claude/LESSONS.md)"
  grep -E "^## Lesson [0-9]+" .claude/LESSONS.md | head -3 | sed 's/^## /- /'
fi

# ── Pending TODOs in repo ─────────────────────────────────────────
TODO_COUNT=$(grep -rE "TODO|FIXME|XXX" --include="*.py" --include="*.ts" --include="*.tsx" \
              --exclude-dir=node_modules --exclude-dir=venv --exclude-dir=.next \
              --exclude-dir=migrations -l . 2>/dev/null | wc -l)
if [ "$TODO_COUNT" -gt 0 ]; then
  echo ""
  echo "### TODOs"
  echo "- $TODO_COUNT file(s) contain TODO/FIXME markers"
fi

# ── Warnings ──────────────────────────────────────────────────────
WARNINGS=()
[ "$DIRTY" -gt 0 ] && WARNINGS+=("Uncommitted changes — run \`git status\`")
[ "$BRANCH" = "main" ] && WARNINGS+=("On \`main\` branch — branch protection blocks direct push. Create feature branch.")

if [ ${#WARNINGS[@]} -gt 0 ]; then
  echo ""
  echo "### ⚠️  Warnings"
  for w in "${WARNINGS[@]}"; do
    echo "- $w"
  done
fi

echo ""
echo "---"
exit 0
