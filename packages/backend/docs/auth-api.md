# Auth API (for the Next.js UI)

Source of truth for sign-in, registration, recovery, devices, 2FA, linking and
onboarding. Everything lives under `/api/auth/` (the Django root, no `/accounts/`
prefix; `/accounts/google/...` is the only allauth URL that remains).

All bodies are JSON (`Content-Type: application/json`); `/api/auth/onboarding/` is JSON-only.
Browsers must send `credentials: 'include'`.

## Conventions

### Cookies (all HttpOnly, `SameSite=Lax`, `Secure` outside DEBUG)

| Cookie | Content | Lifetime |
|---|---|---|
| `access_token` | JWT (15 min, carries `sid`, `current_org`) | `Max-Age` 15 min with `remember`, else browser-session cookie |
| `refresh_token` | `<session id>.<secret>`, path `/api/auth/` (`AUTH_REFRESH_COOKIE_PATH`) | 30 days sliding with `remember`, else browser-session cookie |
| `device_id` | random device id (IP/User-Agent are not part of it) | 1 year |

If the UI is served through a proxy prefix, set `AUTH_REFRESH_COOKIE_PATH` to the
prefixed path; `logout`/failed `refresh` clear the cookie with that same path.

### Errors

Every failure uses one envelope:

```json
{
  "success": false,
  "error": {
    "code": "validation_error",
    "message": "human readable",
    "details": { "password": [{ "code": "password_too_short", "message": "..." }] }
  },
  "detail": "same as message"
}
```

Extra keys may sit inside `error` (`suggestions`, `retry_after`). Status codes used:
`400` validation/credentials, `401` not signed in (call `refresh` once, then send the user to login),
`403` forbidden (also a foreign `Origin` on a state-changing request), `404`, `409` conflict, `429` throttled.

Every state-changing request (POST/PATCH/DELETE, signed in or not, including login/register/Telegram sign-in) with an `Origin` header outside
`CORS_ALLOWED_ORIGINS` / `CSRF_TRUSTED_ORIGINS` / the API host get `403 origin_not_allowed`
(this, plus `SameSite=Lax`, is the CSRF defence; there is no CSRF token to fetch).

### The `user` object

Returned by login, register, Telegram sign-in and `GET /me/`:

```json
{
  "id": "uuid", "username": "ali.valiyev", "first_name": "Ali", "last_name": "Valiyev",
  "display_name": "Ali Valiyev", "email": null, "email_verified": false,
  "phone_number": null, "avatar": null, "preferred_lang": "uz",
  "user_type": "b2c", "is_staff": false, "is_superuser": false,
  "has_password": true, "username_change_available": false,
  "telegram": { "id": 123, "username": "ali", "photo_url": null },
  "primary_organization": { "id": "uuid", "name": "...", "slug": "..." }
}
```

`telegram` and `primary_organization` are `null` when absent. `email` is optional and never
verified today (`email_verified` is true only for Google-provided addresses); it is not used to
sign in or to reset passwords.

### Rate limits

The 5-requests-per-minute tier (per client IP, per user once signed in) covers
`/api/auth/login/`, `/register/`, `/otp/*`, `/password/*`, `/recovery/*`, `/2fa/*`.
Over the limit: `429` with `Retry-After` and
`{"error":"rate_limit_exceeded","tier":"login","retry_after":N}`.
The client IP comes from `TRUSTED_PROXY_CIDRS` only (see `deployment/cloudflare.md`).

On top of that, password sign-in has two failure buckets so a stranger cannot lock the owner out:

* **account + client IP**: after 5 failures in a row the address is locked for 1 min, then 5 min, then 15 min (cap);
  an attacker only locks their own address, the owner signing in from elsewhere is unaffected;
* **account-wide** (guessing from many IPs): the same ladder after `LOGIN_GLOBAL_FREE_ATTEMPTS` (20) failures.
  A device that has signed in to that account before (valid `device_id` cookie) bypasses this lock.

Attempts during a lock are refused without extending it. A wrong 2FA code counts as a failure too. Locked response:

`429 {"error":{"code":"account_locked","retry_after":60,...}}` + `Retry-After`.

The owner is told on Telegram when a lock starts (if Telegram is linked), at most once per hour.

---

## Sign-in

### `POST /api/auth/register/`

Creates the account and signs it in. The terms text is shown without a checkbox; the server
stores `terms_version` + `terms_accepted_at`.

```json
{ "first_name": "Ali", "last_name": "Valiyev", "username": "ali.valiyev",
  "password": "...", "email": "optional@x.uz", "remember": true,
  "acquisition": { "utm_source": "instagram", "utm_campaign": "nov", "start_param": "..." } }
```

* `first_name`, `last_name`: 2-60 characters each. `username`: see *Username rules*. `email`: optional, unverified.
* `remember` defaults to `true`. `acquisition` is optional (first-touch UTM/start-param attribution; unknown keys are dropped).

`201 {"user": {...}}` + cookies. Errors `400 validation_error` with `details` per field:

| Field | Codes |
|---|---|
| `first_name`/`last_name` | `first_name_length`, `last_name_length` |
| `username` | `username_invalid`, `username_reserved`, `username_taken` (response also has `error.suggestions`: 3 free names) |
| `email` | `email_invalid`, `email_taken` |
| `password` | `password_required` and every code under *Password policy* |

### `POST /api/auth/login/`

```json
{ "username": "ali.valiyev", "password": "...", "remember": false, "code": "123456" }
```

`username` is case-insensitive. Email is not a login identifier. `code` (TOTP or backup code)
is only needed when 2FA is on.

| Response | Meaning |
|---|---|
| `200 {"user": {...}}` + cookies | signed in |
| `200 {"need_code": true}` | 2FA is on: repeat the request with `code` (no session yet) |
| `200 {"password_change_required": true, "change_token": "..."}` | platform staff issued a temporary password: call `POST /password/change-required/` with the ticket (no session yet; the ticket is only issued after password and 2FA passed) |
| `400 invalid_credentials` | wrong username/password/inactive/no-password account (same answer for unknown users) |
| `400 invalid_code` | wrong or already-used 2FA code |
| `429 account_locked` | see *Rate limits* |

`remember: true` = persistent cookies and a 30-day sliding session; otherwise browser-session
cookies and a 24 h sliding server session.

### `POST /api/auth/telegram/` (Mini App)

`{"init_data": "<WebApp.initData>", "acquisition": {...}?}`. The signature is verified with the bot
token; initData older than 10 minutes is refused (same for linking). Creates the account on first use (username generated
from the Telegram name, one free change, `start_param` captured as attribution).
`200 {"user": {..., "is_new": true}}` + cookies (persistent). `401 {"error": "..."}` bad signature or older than 10 minutes,
`403` blocked account. (Legacy shape: `error` is a string here.)

### Telegram through the bot (web)

1. `POST /api/auth/tg/init/` (optional body `{"acquisition": {...}}`) -> `{"token", "deep_link", "expires_in": 300}`
   and an HttpOnly `tg_nonce` cookie (path `/api/auth/tg/`) that binds the sign-in to this browser.
   Open `deep_link`; the bot shows which device/IP asked, and the user presses Start and shares *their own* phone contact.
2. Poll `GET /api/auth/tg/status/?token=...` -> `{"status": "pending" | "waiting" | "verified" | "expired"}`.
3. When `verified`: `POST /api/auth/tg/complete/` `{"token", "remember"?}` -> `{"user": {...}}` + cookies
   (`401` if not verified/expired or the `tg_nonce` cookie is missing/wrong, i.e. another browser; `403` blocked).

New accounts get a name-based username (`username_change_available: true`).

### Google

Send the browser to `GET /accounts/google/login/` (full page navigation). After the callback the
cookies are set and the browser is redirected to `FRONTEND_BASE_URL/`. A Google signup creates an
account with a name-based username and a verified email. An existing account is linked
automatically only when its email is already verified; an unverified address (anyone can type
any address at signup) is removed from that account instead and the Google user gets a new one.

### `POST /api/auth/otp/send/` and `POST /api/auth/otp/verify/` (feature flag)

Only when `ENABLE_SMS_OTP=true` (default `false`; otherwise `404 sms_otp_disabled`).
`{"phone"}` -> `{"phone","message"}`; `{"phone","code"}` -> `{"user": {...}}` + cookies.
Codes come from `secrets`, only an HMAC is stored, 3 attempts per code. Note: this path does not
ask for a 2FA code; keep the flag off for accounts that use 2FA until that hook is added.

### `POST /api/auth/refresh/`

No body. Needs the `refresh_token` cookie and the `device_id` cookie it was issued for (the access cookie is
already gone after 15 minutes of inactivity). `200 {"ok": true}` + rotated cookies. `401` (cookies cleared)
when the session is unknown, revoked or expired, or the device cookie is missing/different.
Two tabs refreshing together are fine: inside a 10 s grace window the second request gets only a new
access cookie (no second rotation, so no refresh cookie is set). Replaying the previous refresh token after
the grace window revokes the whole session (theft signal).

### `POST /api/auth/logout/`

Revokes the current device session (found from the refresh cookie, or the access token) and clears
the cookies. Always `200 {"ok": true}`.

### `GET /api/auth/me/` / `PATCH /api/auth/me/`

`GET` -> the `user` object plus `is_profile_complete`, `two_factor_enabled` and
`onboarding: {"is_complete", "missing": [...]}`. `401` when anonymous or when the device was revoked.
`PATCH` accepts `first_name`, `last_name` (2-60), `email` (optional/blank clears it), `preferred_lang`
(`uz|ru|en`); phone numbers can never be set by the client. Errors: `400 validation_error`.

---

## Username rules

3-32 characters from `[a-z0-9_.]`, stored lower-case, unique case-insensitively (also enforced by a
database constraint). Reserved: `admin`, `administrator`, `support`, `root`, `api`, `staff`,
`mashqgoh`, `help`, ... (also with `.`/`_` inserted, e.g. `ad.min`).

### `GET /api/auth/username/check/?username=Ali.Valiyev` (public)

`{"username","valid":true,"available":false,"reason":"username_taken","suggestions":["ali.valiyev417",...]}`
(`reason`: `username_invalid`, `username_reserved`, `username_taken`; `suggestions` has 3 entries when taken).

### `POST /api/auth/username/` (signed in)

`{"username"}`. Telegram/Google accounts have **one** free change
(`username_change_available`); every other case is `403 username_change_not_allowed`.
Validation errors are `400` like registration (`suggestions` when taken). Returns `{"user": {...}}`.

---

## Password

### Password policy (one rule set for register, change, reset, admin)

8-128 characters and at most 72 bytes in UTF-8; not a single repeated character; not in Django's
common-password list; not entirely numeric; no platform words (`milliy`, `sertifikat`, `mashqgoh`,
`yuzdanyuz`, `mock`, ...) and no `123456` variants; not similar to the username/name/email and
not equal to the username.
Codes: `password_too_short`, `password_too_long`, `password_too_many_bytes`, `password_repeated_char`,
`password_too_common`, `password_entirely_numeric`, `password_local_word`, `password_equals_username`,
`password_too_similar`.

### `POST /api/auth/password/change/` (signed in)

`{"current_password", "new_password"}`. Accounts without a password (Telegram/Google) omit
`current_password` and set their first one. Wrong current password counts toward the account lock.
`200 {"ok": true}`; the other devices are signed out, the owner gets a Telegram notice.
Errors: `400 invalid_credentials` (`details.current_password`), `400 validation_error` (`details.new_password`), `429`.

### `POST /api/auth/password/change-required/`

Replaces a temporary password issued by an admin and signs the user in.
`{"change_token", "new_password", "remember"?}` (the ticket from the login response; single use, 10 min; no second
TOTP code is needed) -> `200 {"user": {...}}` + cookies. Alternatively `{"username", "password" (the temporary one),
"code"?, "new_password"}`. `400 invalid_ticket`, `409 not_required` for normal accounts; same errors as login.
Temporary passwords are issued only by platform staff (Django admin action "Vaqtinchalik parol berish"); org admins can not set passwords.

---

## Recovery (no email reset)

* **Telegram link** - `POST /api/auth/recovery/telegram/` `{"username"}` -> always `202 {"ok": true, "message"}`
  (identical for unknown, Telegram-less and inactive accounts). When the account has Telegram, the bot
  sends a link `FRONTEND_BASE_URL/reset-password#token=...` (the token is in the URL *fragment*: read it with
  `location.hash`, it never reaches server logs or Referer; 15 min, single use, only a hash is stored,
  3 per hour per account and 10 per hour per client IP). Requesting another link does not invalidate earlier
  ones (so strangers cannot kill the owner's link); using any link retires the rest.
  Users can also send `/reset` to the bot.
* `POST /api/auth/recovery/confirm/` `{"token", "new_password"}` -> `200 {"ok": true}`; all devices are
  signed out and the account lock is cleared. 2FA stays on. Errors: `400 invalid_token`, `400 validation_error`
  (`details.new_password`; the token survives a rejected password).
* **Platform admin queue** - `POST /api/auth/recovery/request/` `{"username", "contact"}` -> always `202`.
  Staff handle the queue in Django admin (action "Vaqtinchalik parol berish").
* **Org admin** - `POST /api/auth/recovery/org-reset/` (signed in, needs `members:manage`) `{"user_id"}` -> `200 {"ok": true, "delivered": "telegram"}`.
  The admin never sees or sets a password: the member receives the recovery link above on their own Telegram, with a notice
  that an org admin asked for it. Only members of **strictly lower rank** can be targeted (owner > manager > teacher >
  observer > student; custom roles rank just above student), never someone who is equal/higher in any shared org, never
  platform staff or yourself, and never a user outside your organizations (so B2C accounts cannot be taken over).
  `403 forbidden` for all of those (identical answer for unknown ids), `409 no_telegram` when the member has no Telegram
  (use the platform-admin queue), `429` after 3 requests per hour per member.

---

## Devices (sessions)

Up to 3 devices per account; signing in on a 4th revokes the least recently used. An organization
with `single_device_policy` (default on) allows exactly one for its members. IP is not part of a
device's identity.

* `GET /api/auth/sessions/` -> `{"sessions": [{"id","device_label":"Chrome on Windows","browser","os","device_type","country","city","ip_address","remember","created_at","last_used_at","is_current"}]}`.
  `country`/`city` come from Cloudflare headers when the request came through a trusted proxy.
* `DELETE /api/auth/sessions/<uuid>/` -> `200 {"ok": true}` (clears the cookies if it was the current one); `404` for foreign/unknown ids.
* `POST /api/auth/sessions/revoke-others/` -> `200 {"ok": true, "revoked": N}`.

A revoked device loses access immediately (not at access-token expiry).

---

## Two-factor authentication (optional, TOTP)

RFC 6238: SHA-1, 6 digits, 30 s step, +-1 window, each step usable once.

* `GET /api/auth/2fa/` -> `{"enabled", "backup_codes_remaining"}`
* `POST /api/auth/2fa/setup/` -> `{"secret", "otpauth_uri"}` (render the URI as a QR code); `409 already_enabled`
* `POST /api/auth/2fa/confirm/` `{"code"}` -> `{"enabled": true, "backup_codes": ["xxxxx-xxxxx", ... 10]}` (shown once). `400 invalid_code`
* `POST /api/auth/2fa/disable/` `{"password"?, "code"}` (password required when the account has one; code = TOTP or backup code) -> `{"enabled": false}`
* `POST /api/auth/2fa/backup-codes/regenerate/` `{"code"}` -> `{"backup_codes": [...10]}`; the old ones stop working.

Backup codes are single use and stored only as keyed hashes. Login: see `need_code`.
Telegram/Google sign-ins are not asked for a code (the identity provider is the second factor).

---

## Linked accounts (profile)

* `GET /api/auth/linked/` -> `{"username","has_password","telegram":{"linked","username"},"google":{"linked","email"},"can_unlink":{"telegram","google"},"google_connect_url":"/accounts/google/login/?process=connect"}`
* `POST /api/auth/linked/telegram/` `{"init_data"}` (inside the Mini App) -> same payload. `400 telegram_not_verified`, `409 telegram_in_use | already_linked`.
* `POST /api/auth/linked/telegram/start/` -> `{"token","deep_link","expires_in"}`. The user opens the link, presses Start, and
  the bot asks "link your Telegram to *<account>*?" with Yes/No buttons. Poll `GET /api/auth/tg/status/?token=`:
  `pending` -> `confirming` -> `linked | conflict | expired`. A Telegram id is only ever taken from signed initData
  or the owner's own confirmation, never from a number typed in.
* `DELETE /api/auth/linked/telegram/` and `DELETE /api/auth/linked/google/` -> updated payload;
  `400 last_method` when it is the only sign-in method left; `404 not_linked`.
* Link Google by navigating to `google_connect_url` (allauth connect flow); it returns to `FRONTEND_BASE_URL/profile`.

---

## Onboarding

Asked before the user's first **result**, not at signup. Required fields (`missing` lists the unanswered ones):
`goals` + `primary_goal`, `dtm_direction_or_subjects` (only when a chosen goal has `requires_direction`),
`test_language`, `target` (score or grade), `graduation_year`, `region`, `district`, `birth_date`.
Other apps gate result screens with `apps.accounts.permissions.OnboardingComplete`
(`permission_classes = [IsAuthenticated, OnboardingComplete]`) or `apps.accounts.services.onboarding.is_complete(user)`.

### `GET /api/auth/onboarding/options/` (public)

Reference data for the form: `goals`, `dtm_directions` (with latest `passing_score`), `regions` (with `districts[{id,name_uz,name_ru}]`),
`exam_calendar`, `test_languages`, `target_grades`, `certificate_types`, `acquisition_answers`, `target_score_max` (189), `graduation_year_range`.
The tables are filled by staff in Django admin.

### `GET /api/auth/onboarding/` -> state, `PATCH /api/auth/onboarding/` (partial)

```json
{ "goals": ["dtm", "ielts"], "primary_goal": "dtm",
  "dtm_direction": "med", "subject_1": null, "subject_2": null,
  "test_language": "uz", "target_score": 160, "target_grade": null,
  "exam_date": "2027-07-01", "graduation_year": 2026,
  "region": 1, "district": 12, "birth_date": "2008-05-17",
  "parent_telegram": "@parent_name",
  "acquisition_answer": "friend", "acquisition_note": "...",
  "certificates": [{ "cert_type": "ielts", "subject": "", "level": "7.0", "date": "2025-03-01" }] }
```

Rules (all failures are `400 validation_error` with `details`, and nothing is saved): goals must exist and be active, exactly one
primary (taken from the only goal when there is one); `dtm_direction` fills the subject pair, or send `subject_1` + `subject_2`;
`test_language` `uz|ru|kaa`; `target_score` 1-189 integer, `target_grade` `A+|A|B+|B|C+|C`; `exam_date` within the next 3 years;
`graduation_year` (a YEAR, not a grade) within -60/+8 years; `region` is the region `code`, `district` the district `id` and must belong
to it (changing the region needs the district too); `birth_date` ISO date, age 10-80; `parent_telegram` a username or t.me link;
`certificates` replaces the whole list (max 20, date not in the future).
Response: the state plus `is_complete`, `missing`, `org_consents`.

### `POST /api/auth/onboarding/org-invite/` `{"code": "<invite uuid>", "consent": true}`

Joins the organization with the role of the invite. `consent` must be exactly `true` ("<centre> will see your results"):
`400 consent_required` otherwise, `404 invalid_invite` for unknown/expired/used-up/disabled invites or inactive organizations.
-> `{"organization": {"id","name"}}`. Withdraw with `DELETE /api/auth/onboarding/org-consent/<uuid>/` (the organization id).

---

## Other

* `POST /api/auth/gdpr/erasure/` `{"confirm": true}`: anonymise the account (unchanged).
* `POST /api/bot/webhook/`: Telegram webhook (secret header required; handles `/start`, contact, `/reset` and the link buttons).

## Settings and environment

| Setting / env | Default | Meaning |
|---|---|---|
| `FRONTEND_BASE_URL` | `http://localhost:3000` | OAuth redirects, recovery links, anonymous home redirect |
| `TERMS_VERSION` | `2026-11-12` | stored with the signup consent |
| `ENABLE_SMS_OTP` | `false` | SMS OTP endpoints |
| `AUTH_MAX_DEVICES` | `3` | devices per account (orgs with `single_device_policy`: 1) |
| `AUTH_REMEMBER_DAYS` / `AUTH_SESSION_HOURS` | `30` / `24` | sliding session lengths |
| `AUTH_REFRESH_COOKIE_PATH` | `/api/auth/` | refresh cookie path |
| `LOGIN_FAILURE_FREE_ATTEMPTS`, `LOGIN_GLOBAL_FREE_ATTEMPTS`, `LOGIN_LOCK_LADDER_SECONDS` | `5`, `20`, `(60, 300, 900)` | per-IP and account-wide lock |
| `TOTP_ENCRYPTION_KEY` | derived from `SECRET_KEY` | key for stored 2FA secrets (set it to rotate `SECRET_KEY` independently) |
| `TRUSTED_PROXY_CIDRS` | empty | proxies whose forwarding headers are believed |
