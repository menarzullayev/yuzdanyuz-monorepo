# Personal data: erasure, export and retention

Audit item C22. Code: `apps/accounts/gdpr.py`, `apps/accounts/services/erasure.py`,
task `accounts.process_due_erasures` (Celery beat, hourly, `batch` queue).

## Export (right of access / portability)

`GET /api/auth/gdpr/export/` returns a JSON file with the signed-in user's own data:
profile, onboarding answers, memberships, result-sharing consents, certificates, device
sessions, exam attempts, wallet and wallet transactions, payments, and erasure requests.
Hashes, tokens and encrypted fields are left out. The export covers every organisation
the user belongs to (the org filter is lifted for that request; every query is filtered
by the user).

## Erasure (right to be forgotten)

Email is optional on this platform, so confirmation does not use email:

1. `POST /api/auth/gdpr/erasure/` with `{"password": ...}` — re-authentication; the request
   is scheduled at once. Without a password, if the account has Telegram linked, the bot
   sends a 6-digit code (15 minutes, 5 attempts, stored only as an HMAC) and
   `POST /api/auth/gdpr/erasure/confirm/ {"code": ...}` schedules it. An account with
   neither gets `reauth_required`.
2. Cooling-off: `GDPR_ERASURE_COOLING_OFF_HOURS` (default 72). Until then
   `POST /api/auth/gdpr/erasure/cancel/` cancels it; `GET /api/auth/gdpr/erasure/` shows it.
3. The hourly task anonymises the account in a single transaction. Any error rolls the
   whole erasure back, is stored on the request (`status=failed`, `error`), logged with its
   traceback (Sentry) and fails the task run; an admin fixes the cause and sets the request
   back to `scheduled`.

What the erasure does:

| Data | Action |
|------|--------|
| User row: username, email, phone, names, Telegram id/username/photo/language, birth year | replaced with `deleted-<id>` / emptied; password unusable; account inactive |
| Avatar file | deleted from storage |
| `simple_history` rows of the user (old emails, phones, names) | deleted |
| Device sessions, 2FA device and backup codes, recovery tokens, password-reset requests, onboarding profile, external certificates, OTP codes for the phone, allauth email addresses and social accounts | deleted |
| Wallet transactions, payment intents, exam attempts and answers, open-ended submissions | soft-deleted, free-text fields emptied (`pii_redacted`) |
| Exam analytics events | soft-deleted |

## What is kept, and for how long

| Data | Why | Retention |
|------|-----|-----------|
| Anonymised user row | foreign key target of results and transactions | as long as those rows |
| Wallet transactions and payment records (amounts, dates, no free text) | accounting | 5 years, then deleted |
| Exam attempts (scores, no answers' free text) | organisation statistics already reported | 5 years |
| `ErasureRequest` (user id, dates, status, what was redacted) | proof the request was handled | 5 years |
| Backups | disaster recovery; encrypted, 30-day rotation (`backup.retentionDays`) | erased data leaves the backups within 30 days; a restore must re-run completed erasures (their `ErasureRequest` rows say which users) |
| Application logs | do not contain email, phone or names from the erasure path | log platform retention |

The 5-year deletions are not automated yet; they are a scheduled task to add once the first
records reach that age (earliest 2031).
