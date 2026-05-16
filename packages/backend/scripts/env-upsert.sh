#!/usr/bin/env bash
# Idempotent .env upsert — mavjud KEY=... qatorini almashtiradi yoki yo'q bo'lsa qo'shadi.
# Maqsad: secret'larni qo'lda kiritmasdan, script orqali xavfsiz update qilish.
#
# Foydalanish:
#   ./scripts/env-upsert.sh KEY VALUE
#
# Backup yaratiladi (.env.bak.<timestamp>) — yoki tekshiring keyin o'chiring.

set -euo pipefail

ENV_FILE="$(dirname "$0")/../.env"
KEY="${1:?KEY argument required}"
VALUE="${2-}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: .env not found at $ENV_FILE" >&2
    exit 1
fi

# Backup (faqat birinchi marta — keyingi run'larda allaqachon bo'lsa skip)
BAK="${ENV_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
cp "$ENV_FILE" "$BAK"

# sed delimiter sifatida `|` ishlatamiz (= belgi value'da bo'lishi mumkin)
# Escape | va & belgilarini value'da
ESCAPED_VALUE=$(printf '%s' "$VALUE" | sed 's/[|&]/\\&/g')

if grep -qE "^${KEY}=" "$ENV_FILE"; then
    sed -i.tmp "s|^${KEY}=.*|${KEY}=${ESCAPED_VALUE}|" "$ENV_FILE"
    rm -f "${ENV_FILE}.tmp"
    echo "✓ updated: $KEY"
else
    # Qator oxirida newline mavjudligini ta'minlash
    [[ -n "$(tail -c1 "$ENV_FILE")" ]] && echo "" >> "$ENV_FILE"
    echo "${KEY}=${VALUE}" >> "$ENV_FILE"
    echo "✓ added:   $KEY"
fi
