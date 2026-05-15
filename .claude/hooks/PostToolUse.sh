#!/bin/bash
# PostToolUse hook — runs after Edit/Write/MultiEdit
# Receives JSON on stdin with tool_name, tool_input.file_path, etc.
# Exit 0 = silent OK, 1 = warning (printed to user), 2 = block

set -euo pipefail

# Read tool input
INPUT=$(cat)
FILE=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")

# Bo'sh path → silent exit
[ -z "$FILE" ] && exit 0

# Repo root'ga o'tish
cd "$(dirname "$0")/../.." || exit 0

WARNINGS=()
BLOCKS=()

# ── 1. Hard guardrail: .env files ─────────────────────────────────
case "$FILE" in
  *.env|*.env.*|*credentials*|*.key)
    if echo "$INPUT" | grep -qE '"tool_name":"(Write|Edit|MultiEdit)"'; then
      BLOCKS+=("🚫 BLOCKED: Refusing to modify secret file: $FILE")
    fi
    ;;
esac

# ── 2. Python files: ruff format + check ──────────────────────────
if [[ "$FILE" == *.py ]] && [[ "$FILE" != *migrations* ]]; then
  if [ -x packages/backend/venv/bin/ruff ]; then
    REL="${FILE#$(pwd)/}"
    packages/backend/venv/bin/ruff check --fix --quiet "$FILE" 2>/dev/null || \
      WARNINGS+=("⚠️  ruff: linting issues in $REL")
    packages/backend/venv/bin/ruff format --quiet "$FILE" 2>/dev/null || true
  fi
fi

# ── 3. Migration files: RLS safety check ──────────────────────────
if [[ "$FILE" == *migrations*.py ]]; then
  if grep -qE "SET\s+app\.[a-z_]+\s*=\s*NULL" "$FILE" 2>/dev/null; then
    BLOCKS+=("🚫 BLOCKED in $FILE: Use 'RESET app.X;' not 'SET app.X = NULL;' (PostgreSQL syntax). See LESSONS.md #01")
  fi
  if grep -qE "DROP\s+(TABLE|COLUMN)" "$FILE" 2>/dev/null; then
    WARNINGS+=("⚠️  $FILE: Contains DROP — verify backup + downtime plan")
  fi
fi

# ── 4. Models: TenantTimestampMixin check ─────────────────────────
if [[ "$FILE" == *apps/*/models.py ]] && [[ "$FILE" != *migrations* ]]; then
  if grep -qE "^class \w+\(models\.Model\):" "$FILE" 2>/dev/null && \
     ! grep -qE "TenantTimestampMixin|TimestampMixin|class Meta:.*abstract.*=.*True" "$FILE" 2>/dev/null; then
    WARNINGS+=("⚠️  $FILE: Has Model subclass without TenantTimestampMixin. Tenant-aware? See CLAUDE.md")
  fi
fi

# ── 5. TypeScript: prettier (if installed) ────────────────────────
if [[ "$FILE" == *.ts ]] || [[ "$FILE" == *.tsx ]]; then
  if [ -x node_modules/.bin/prettier ]; then
    node_modules/.bin/prettier --write --log-level=silent "$FILE" 2>/dev/null || true
  fi
fi

# ── Output ────────────────────────────────────────────────────────
for msg in "${BLOCKS[@]:-}"; do
  [ -n "$msg" ] && echo "$msg" >&2
done
for msg in "${WARNINGS[@]:-}"; do
  [ -n "$msg" ] && echo "$msg" >&2
done

# Exit codes: 2 = block, 0 = OK (warnings still shown but don't block)
[ ${#BLOCKS[@]} -gt 0 ] && exit 2
exit 0
