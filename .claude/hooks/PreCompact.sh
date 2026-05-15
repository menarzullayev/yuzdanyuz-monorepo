#!/bin/bash
# PreCompact hook — fires before context compaction.
# Save critical state that must survive context window reset.

set -euo pipefail
cd "$(dirname "$0")/../.." || exit 0

# Output is shown to Claude as preserved context after compaction.
# Keep it CONCISE — must fit within the new context budget.

cat <<EOF
## 💾 Preserved State (PreCompact)

EOF

# Current branch + status (1-line)
BRANCH=$(git branch --show-current 2>/dev/null || echo "?")
DIRTY=$(git status --porcelain 2>/dev/null | wc -l)
LAST=$(git log --oneline -1 2>/dev/null | head -c 100)

echo "**Git**: \`$BRANCH\` ($DIRTY uncommitted) — $LAST"

# Latest pytest cache result (if exists)
if [ -d packages/backend/.pytest_cache ]; then
  LASTFAILED=$(find packages/backend/.pytest_cache -name "lastfailed" 2>/dev/null | head -1)
  if [ -n "$LASTFAILED" ] && [ -s "$LASTFAILED" ]; then
    FAILCOUNT=$(grep -c '"' "$LASTFAILED" 2>/dev/null || echo 0)
    echo "**Pytest**: $((FAILCOUNT/2)) failing test(s) cached"
  fi
fi

# Open PRs
if command -v gh >/dev/null 2>&1; then
  PRS=$(gh pr list --json number,title --limit 5 2>/dev/null | \
        python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    if d:
        print('**Open PRs**:')
        for p in d:
            print(f\"  - #{p['number']}: {p['title'][:60]}\")
except: pass
" 2>/dev/null)
  [ -n "$PRS" ] && echo "$PRS"
fi

# Currently in-progress (from todo file if exists)
if [ -f .claude/CLAUDE.local.md ]; then
  CURRENT=$(grep -A 1 "Currently Working On" .claude/CLAUDE.local.md | tail -1)
  if [ -n "$CURRENT" ] && [ "$CURRENT" != "(empty — set this at session start)" ]; then
    echo "**Current task**: $CURRENT"
  fi
fi

echo ""
echo "_Read CLAUDE.md and LESSONS.md for full context._"
exit 0
