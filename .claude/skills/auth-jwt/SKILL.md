---
name: auth-jwt
description: JWT authentication, device fingerprinting, and session management patterns. Use when modifying auth flow, login views, JWT token claims, device sessions, or refresh token logic.
---

# JWT + Device Fingerprinting

This project uses **custom JWT** (not DRF SimpleJWT) with Redis session locking for one-account-one-device enforcement.

---

## Token Pair Creation

**Always include `current_org` claim** for multi-tenant isolation:
```python
from apps.accounts.services.token_service import create_token_pair

# At login, after authenticating user
fingerprint = make_fingerprint(request.META.get('HTTP_USER_AGENT'),
                               request.META.get('REMOTE_ADDR'))
access, refresh = create_token_pair(
    user=user,
    fingerprint=fingerprint,
    org=user.primary_organization,   # JWT current_org claim
)
```

**Why `current_org`**: TenantMiddleware reads it from JWT to set tenant context. Without it, queries fall back to `primary_organization` or `None`.

---

## JWT Payload Structure

```json
{
  "sub": "<user.pk>",
  "type": "access",
  "exp": 1800,                          // 30 min
  "fp": "<sha256 fingerprint>",
  "current_org": "<org.uuid>",          // multi-tenant context
  "iat": 1234567890
}
```

Refresh token: same but `type: "refresh"`, `exp: 7d`.

---

## Verification

```python
from apps.accounts.services.token_service import verify_access_token

user_id = verify_access_token(token)   # Returns str(user.pk) or None
if user_id is None:
    raise PermissionDenied("Invalid token")
```

`verify_access_token`:
- Validates signature
- Checks expiration
- Confirms `type == "access"` (refresh tokens rejected)
- Returns `None` on any failure (no exception leakage)

---

## Cookie Strategy

**Always**:
- `httpOnly: true` (no JS access)
- `secure: true` in prod (HTTPS only)
- `samesite: 'Lax'` (CSRF protection)
- Path: `/`

**Done by**: `core.middleware.jwt_cookie_writer.JWTCookieWriterMiddleware`

---

## Device Fingerprinting

Single-device policy (default org setting):
```python
# 1st login: Redis SET fp:<user_id> = <fingerprint>
# 2nd login (different device): old session revoked, Redis OVERWRITES key
# Existing session ping: DeviceCheckMiddleware verifies fp matches
# Mismatch: force logout + clear cookie
```

**Toggle per-org**:
```python
org.single_device_policy = False   # allow multi-device
org.save()
```

---

## OAuth Flow (Google/Yandex/Apple)

`django-allauth` handles OAuth → `AccountAdapter.login()` creates JWT → `JWTCookieWriterMiddleware` writes cookie.

Key file: `apps/accounts/adapters.py`

---

## Anti-Patterns (DO NOT)

### ❌ Token in localStorage
```javascript
// WRONG — XSS vulnerable
localStorage.setItem('token', token);

// CORRECT — httpOnly cookie set by backend, frontend uses fetch with credentials
fetch('/api/x', { credentials: 'include' })
```

### ❌ Long-lived access tokens
Access token: max 30 min. Use refresh for sessions.

### ❌ Forgetting `current_org` claim
```python
# WRONG — multi-tenant user gets NO tenant context
access, refresh = create_token_pair(user, fingerprint)

# CORRECT
access, refresh = create_token_pair(user, fingerprint, org=user.primary_organization)
```

### ❌ Custom JWT verification (use service)
```python
# WRONG — easy to miss type check, signature, etc.
import jwt
payload = jwt.decode(token, SECRET_KEY)

# CORRECT — service handles all checks
from apps.accounts.services.token_service import verify_access_token
user_id = verify_access_token(token)
```

---

## Verification

```bash
cd packages/backend
./venv/bin/pytest tests/security/test_jwt_security.py -v
./venv/bin/pytest apps/accounts/tests.py::TestDeviceFingerprint -v
```

---

## Reference Files

- `packages/backend/apps/accounts/services/token_service.py` — JWT creation/verification
- `packages/backend/apps/accounts/middleware.py` — DeviceCheckMiddleware, DeviceLogoutMiddleware
- `packages/backend/apps/accounts/adapters.py` — allauth → JWT bridge
- `packages/backend/apps/accounts/backends.py` — JWTBackend (auth backend)
- `packages/backend/core/middleware/jwt_auth.py` — cookie → request.user
- `packages/backend/core/middleware/jwt_cookie_writer.py` — JWT → cookie
- `packages/backend/tests/security/test_jwt_security.py` — security tests
