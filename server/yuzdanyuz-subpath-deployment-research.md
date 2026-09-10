# YuzDanYuz Subpath Deployment Research

**Sana:** 2026-05-16  
**Maqsad:** `hsm.sammu.uz/<subpath>/` orqali Django backend'ga ulanish, mavjud Yii2 kasalxona saytiga xalaqit qilmagan holda  
**TL;DR:** Loyiha egasi infrastrukturani allaqachon yaratgan. Faqat **3 ta kichik tuzatish** kerak — Apache reload kerakmas

---

## 1. Topilma — `/yuzdanyuz/` allaqachon mavjud

`/home/hsm/web/hsm.sammu.uz/public_html/yuzdanyuz/` papkasi **15-may 2026'da yaratilgan** va to'liq subpath proxy uchun mo'ljallangan:

```
yuzdanyuz/
├── .htaccess           (302 bytes — RewriteBase + rewrite to index.php)
├── index.php           (6807 bytes — PHP cURL reverse proxy)
└── static -> /home/hsm/apps/yuzdanyuz/staticfiles  (symlink — broken, eski path)
```

### 1.1 `.htaccess` (mavjud, ishlaydi)

```apache
<IfModule mod_rewrite.c>
    RewriteEngine On
    RewriteBase /yuzdanyuz/
    
    RewriteCond %{REQUEST_FILENAME} !-f
    RewriteCond %{REQUEST_FILENAME} !-d
    
    RewriteRule ^(.*)$ index.php [L]
</IfModule>
```

**Tushuntirish:** `/yuzdanyuz/<anything>` → `/yuzdanyuz/index.php` (real fayl/papka bo'lmasa). Static fayllar (`/yuzdanyuz/static/*`) symlink orqali to'g'ridan-to'g'ri serve qilinadi.

### 1.2 `index.php` — PHP-based reverse proxy

Asosiy logika:
- REQUEST_URI'dan `/yuzdanyuz` prefix'ni olib tashlaydi
- cURL bilan `http://127.0.0.1:8013` ga forward qiladi
- `X-Script-Name: /yuzdanyuz`, `X-Forwarded-For/Proto/Host` headers qo'shadi
- Response body va headers'ni client'ga qaytaradi
- POST/PUT/PATCH/DELETE body'ni forward qiladi
- Xato bo'lsa, batafsil 502 page (DEBUG=true)

**Kritik kod (port hard-coded):**
```php
$django_url = 'http://127.0.0.1:8013' . $path;
```

### 1.3 Django settings — FORCE_SCRIPT_NAME allaqachon tayyor

[`core/settings/dev.py`](Web_Projects/yuzdanyuz-monorepo/packages/backend/core/settings/dev.py):
```python
# PHP proxy orqali https://hsm.sammu.uz/yuzdanyuz/ da ishlash uchun.
# Faqat FORCE_SCRIPT_NAME env mavjud bo'lganda yoqamiz — direct gunicorn
# (lokal :8001) kirishida bu yo'q va Django URL'larni prefix'siz generate qiladi.
CSRF_TRUSTED_ORIGINS = ['https://hsm.sammu.uz']
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

_script_name = os.getenv('FORCE_SCRIPT_NAME', '').rstrip('/')
if _script_name:
    FORCE_SCRIPT_NAME = _script_name
    STATIC_URL = f'{_script_name}/static/'
    LOGIN_REDIRECT_URL = f'{_script_name}/'
    LOGIN_URL = f'{_script_name}/login/'
```

Ya'ni `FORCE_SCRIPT_NAME=/yuzdanyuz` env-var qo'yilganda Django avtomatik:
- URL'larni `/yuzdanyuz/<path>` bilan generate qiladi
- STATIC_URL'ni `/yuzdanyuz/static/`
- Login redirects'ni `/yuzdanyuz/login/`
- CSRF_TRUSTED_ORIGINS allaqachon `hsm.sammu.uz`

---

## 2. Architecture — Hozirgi va Maqsad

### 2.1 Hozirgi traffic flow

```
                          ┌──────────────────────────────────┐
                          │  Client (browser, HTTPS)         │
                          └─────────────┬────────────────────┘
                                        │ https://hsm.sammu.uz/...
                                        ▼
                          ┌──────────────────────────────────┐
                          │  nginx :443 (HestiaCP, SSL term) │
                          │  Static fayllar → public_html    │
                          │  Boshqalar → Apache :8443        │
                          └─────────────┬────────────────────┘
                                        │ proxy_pass https://...:8443
                                        ▼
                          ┌──────────────────────────────────┐
                          │  Apache :8443 (HestiaCP)         │
                          │  PHP fayllar → PHP-FPM           │
                          │  .htaccess rewrite ishlatadi     │
                          └─────────────┬────────────────────┘
                                        │
                  ┌─────────────────────┼─────────────────────┐
                  │                     │                     │
                  ▼                     ▼                     ▼
              /yuzdanyuz/*      / (Yii2 PHP app)        /static/*
              ↓                 ↓                       (nginx serves
              index.php         index.php (Yii2)         directly)
              ↓ cURL
              http://127.0.0.1:8013  ← PROBLEM: Django :8001'da!
              (Django GUNICORN)
```

### 2.2 Maqsadli traffic flow

Bir nechta o'zgartirish bilan **bir xil ko'rinish, lekin ishlaydigan**:

```
                          ...
                  /yuzdanyuz/*  →  index.php (cURL)  →  http://127.0.0.1:8001
                                                       ↑
                                          (port to'g'rilangan)
                  /yuzdanyuz/static/*  →  symlink  →  yuzdanyuz-monorepo/packages/backend/staticfiles
                                                       ↑
                                          (symlink yangilangan)
                  ASGI/WebSocket  →  (KEYINGI bosqich, hozir kerak emas)
```

---

## 3. Aniqlangan muammolar va sabablari

### 3.1 🔴 Port mismatch (jiddiy)

**Muammo:** `index.php` hardcoded `:8013`'ga forward qiladi  
**Haqiqat:** Django gunicorn `:8001`'da ishlaydi (supervisord conf)  
**Natija:** `/yuzdanyuz/` → 502 Bad Gateway "Cannot connect to Django backend"

```bash
$ curl https://hsm.sammu.uz/yuzdanyuz/
HTTP 502 "Cannot connect to Django backend"
Location: http://127.0.0.1:8013   ← noto'g'ri port
```

### 3.2 🟡 Symlink eskirgan

**Muammo:** `/yuzdanyuz/static` → `/home/hsm/apps/yuzdanyuz/staticfiles`  
**Haqiqat:** Yangi monorepo'da: `/home/hsm/apps/yuzdanyuz-monorepo/packages/backend/staticfiles/`  
**Natija:** `/yuzdanyuz/static/admin/css/base.css` → 404

### 3.3 🟡 FORCE_SCRIPT_NAME .env'da yo'q

**Muammo:** Django settings env-var support qiladi, lekin `.env` faylda set qilinmagan  
**Natija:** Django URL'larni prefix'siz generate qiladi → links broken, redirects noto'g'ri

### 3.4 🟢 Apache mod_proxy_http ehtimol yo'q (alohida masala)

**POC test natijasi:** `.htaccess`'da `RewriteRule [P]` → HTTP 500 Apache error  
**Tushuntirish:** mod_proxy yoqilgan, lekin mod_proxy_http YO'Q (admin yordamisiz yoqilolmaydi)  
**Lekin muhim emas** — biz PHP-based proxy ishlatamiz, mod_proxy kerakmas

### 3.5 🟢 WebSocket (kelajakda)

**Muammo:** PHP proxy WebSocket'ni qo'llab-quvvatlamaydi  
**Bonus problem:** Nginx config'da `proxy_hide_header Upgrade;` bor — bu nginx orqali WebSocket'ni o'chiradi  
**Hozirgi yondashuv:** WebSocket'ni keyingi bosqichga qoldirish (real-time features hozir kerakmas)

---

## 4. Tavsiya etilgan yo'l — 5 daqiqalik fix

### 4.1 Bizning afzalligimiz

Quyidagilar **hammasi mavjud va ishlaydi**:
- `.htaccess` rewrite engine ✅
- PHP-FPM `proc_open` (kelajakda kerak bo'lsa)
- Django FORCE_SCRIPT_NAME env-var support ✅
- supervisorctl jail SSH'dan ✅ (oldingi sessiyada o'rnatilgan)

### 4.2 Step-by-step plan (jami ~5 daqiqa, Apache reload YO'Q)

#### STEP 1 — Port fix (2 daqiqa)

```bash
# /yuzdanyuz/index.php ichida 8013 → 8001 o'zgartirish
sed -i "s|127.0.0.1:8013|127.0.0.1:8001|g" \
    /home/hsm/web/hsm.sammu.uz/public_html/yuzdanyuz/index.php

# Verify:
grep "127.0.0.1" /home/hsm/web/hsm.sammu.uz/public_html/yuzdanyuz/index.php
# Kutilgan: "127.0.0.1:8001" (8013 yo'q)
```

#### STEP 2 — Static symlink fix (1 daqiqa)

```bash
# Eski symlink'ni o'chirib, yangisini yaratish
cd /home/hsm/web/hsm.sammu.uz/public_html/yuzdanyuz/
rm static
ln -s /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/staticfiles static
ls -la static
# Kutilgan: symlink → /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/staticfiles/
```

#### STEP 3 — `.env`'ga FORCE_SCRIPT_NAME qo'shish (1 daqiqa)

```bash
# Backend .env fayl'ga env var qo'shish:
ENV_FILE=/home/hsm/apps/yuzdanyuz-monorepo/packages/backend/.env

# Bo'lmasa qo'sh, bo'lsa yangila:
if grep -q "^FORCE_SCRIPT_NAME=" $ENV_FILE; then
    sed -i 's|^FORCE_SCRIPT_NAME=.*|FORCE_SCRIPT_NAME=/yuzdanyuz|' $ENV_FILE
else
    echo "FORCE_SCRIPT_NAME=/yuzdanyuz" >> $ENV_FILE
fi

# Verify:
grep "FORCE_SCRIPT_NAME" $ENV_FILE
```

#### STEP 4 — Django restart (1 daqiqa)

```bash
# supervisorctl jail'dan ishlaydi:
/home/hsm/.local/bin/supervisorctl -c /home/hsm/.config/supervisor/supervisord.conf \
    restart yuzdanyuz-web yuzdanyuz-asgi yuzdanyuz-celery-worker yuzdanyuz-celery-beat

# Status verify:
/home/hsm/.local/bin/supervisorctl -c /home/hsm/.config/supervisor/supervisord.conf status
```

#### STEP 5 — Test (1 daqiqa)

```bash
# Public URL test'lar:
curl -s -w "\nHTTP %{http_code}\n" https://hsm.sammu.uz/yuzdanyuz/health/
# Kutilgan: {"status": "ok"} + HTTP 200

curl -s -o /dev/null -w "HTTP %{http_code}\n" https://hsm.sammu.uz/yuzdanyuz/admin/
# Kutilgan: HTTP 302 (redirect to login) yoki 200 (login page)

curl -s -o /dev/null -w "HTTP %{http_code}\n" https://hsm.sammu.uz/yuzdanyuz/static/admin/css/base.css
# Kutilgan: HTTP 200 (Django admin CSS)

curl -s -o /dev/null -w "HTTP %{http_code}\n" https://hsm.sammu.uz/
# Kutilgan: HTTP 200 (Yii2 kasalxona sayti — buzilmagan!)
```

---

## 5. Path naming (`/yuzdanyuz/` vs `/yuzdanbir/`)

Siz xabaringizda **`/yuzdanbir/`** yozgansiz, lekin:
- Existing folder: **`/yuzdanyuz/`**
- App name: **YuzDanYuz** (Uzbek: "100/100")
- Django settings comment: `https://hsm.sammu.uz/yuzdanyuz/`

**Ehtimol typo.** Lekin agar haqiqatan `/yuzdanbir/` kerak bo'lsa, biz `/yuzdanyuz/` papkasini ko'chirib o'tkazishimiz mumkin:

```bash
cd /home/hsm/web/hsm.sammu.uz/public_html/
cp -r yuzdanyuz yuzdanbir
# index.php va .htaccess ichida 'yuzdanyuz' → 'yuzdanbir' o'zgartirish
sed -i 's|/yuzdanyuz/|/yuzdanbir/|g; s|yuzdanyuz|yuzdanbir|g' yuzdanbir/.htaccess yuzdanbir/index.php
# Va Django settings FORCE_SCRIPT_NAME=/yuzdanbir
```

**Tavsiyam:** `/yuzdanyuz/` ishlatish — infra tayyor, brand bilan mos.

---

## 6. Rollback plan (xatolik bo'lsa)

Hech qanday riskli o'zgartirish yo'q, lekin har holatda:

### Step 1 (port fix) rollback:
```bash
sed -i "s|127.0.0.1:8001|127.0.0.1:8013|g" /home/hsm/web/hsm.sammu.uz/public_html/yuzdanyuz/index.php
```

### Step 2 (symlink) rollback:
```bash
cd /home/hsm/web/hsm.sammu.uz/public_html/yuzdanyuz/
rm static
ln -s /home/hsm/apps/yuzdanyuz/staticfiles static   # eski (broken) symlink
```

### Step 3 (.env) rollback:
```bash
sed -i '/^FORCE_SCRIPT_NAME=/d' /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/.env
```

### Step 4 (Django restart) rollback:
```bash
supervisorctl restart yuzdanyuz-web yuzdanyuz-asgi yuzdanyuz-celery-worker yuzdanyuz-celery-beat
# Yana eski (ENV'siz) state'da ishga tushadi
```

**Yii2 kasalxona sayti** — biz unga **hech narsa qilmaganmiz** (faqat `/yuzdanyuz/` folder), buzilish ehtimoli yo'q.

---

## 7. Kelajak optimizatsiyalar (hozir kerakmas)

### 7.1 WebSocket support (real-time chat, leaderboard)

PHP proxy WebSocket'ni qo'llab-quvvatlamaydi. Echim variantlari:
- **A.** Apache `mod_proxy_wstunnel` yoqish (admin yordami kerak)
- **B.** nginx `proxy_hide_header Upgrade;` ni o'chirib, `location /yuzdanyuz/ws/` qo'shish (HestiaCP nginx hook orqali, lekin admin nginx reload kerak)
- **C.** Alohida subdomain `ws.hsm.sammu.uz` — eng toza variant, lekin DNS + SSL setup kerak

### 7.2 Performance — Apache native ProxyPass

PHP proxy har request uchun PHP-FPM jarayoni va cURL spawn qiladi (~10-20 ms overhead). Apache native `ProxyPass`:
- Tezroq (~1-2 ms overhead)
- Lekin mod_proxy_http yoqish kerak (admin yordami)
- Va HestiaCP `apache2.conf_yuzdanyuz` hook orqali config qo'shish kerak

### 7.3 Static files — nginx direct serve

Hozir `/yuzdanyuz/static/*` Apache orqali keladi. Nginx config'ga `location /yuzdanyuz/static/ { alias .../staticfiles/; }` qo'shsak — to'g'ridan-to'g'ri nginx serve qiladi (faster, no Apache). HestiaCP `nginx.ssl.conf_yuzdanyuz` hook bor, lekin nginx reload kerak.

---

## 8. Comparison — alternativlar bilan

| Yondashuv | Apache reload | Implement vaqti | Performance | WebSocket | Maintenance |
|---|---|---|---|---|---|
| **A. Existing PHP proxy fix (TAVSIYA)** | ❌ Yo'q | 5 min | 🟡 Past (~15ms overhead) | ❌ Yo'q | 🟢 Oddiy |
| B. Apache `apache2.conf_yuzdanyuz` ProxyPass | ✅ Kerak (admin) | 15 min | 🟢 Yuqori | 🟡 Mumkin | 🟢 Oddiy |
| C. Nginx `nginx.ssl.conf_yuzdanyuz` proxy_pass | ✅ Kerak (admin) | 15 min | 🟢 Eng yuqori | 🟢 Mumkin | 🟢 Oddiy |
| D. Alohida subdomain `api.hsm.sammu.uz` | ✅ Kerak (admin) + DNS + SSL | 30+ min | 🟢 Eng yuqori | 🟢 Toza | 🟢 Oddiy |

**Hozirgi kontekst (no admin, pre-launch):** Variant A — eng pragmatik tanlov. Launch'gacha keyin **C** ga ko'chish mumkin.

---

## 9. Xulosa — Action items

### Immediate (5 daqiqalik fix)

1. ✅ Port fix: `index.php` 8013 → 8001
2. ✅ Symlink fix: `static` → monorepo path
3. ✅ Env var: `FORCE_SCRIPT_NAME=/yuzdanyuz` `.env`'ga
4. ✅ Django restart: `supervisorctl restart`
5. ✅ Test 4 ta URL: health, admin, static, Yii2 homepage

### Decision needed (siz)

- ❓ Path: `/yuzdanyuz/` (existing) yoki `/yuzdanbir/` (yangi)?
- ❓ Hozir bajaramizmi, yoki keyingi sessiyaga qoldiramizmi?

### Future (launch'dan oldin)

- 📋 WebSocket: agar real-time features kerak bo'lsa
- 📋 Performance: agar PHP proxy overhead seziladigan bo'lsa
- 📋 Frontend integration: yangi `packages/frontend-web/` `/yuzdanyuz/api/v1/` ga so'rovlar yuborishi kerak

---

## 10. Verification checklist

Bajarib bo'lgandan keyin:

- [ ] `curl https://hsm.sammu.uz/yuzdanyuz/health/` → JSON `{"status": "ok"}`, HTTP 200
- [ ] `curl https://hsm.sammu.uz/yuzdanyuz/admin/` → HTTP 302 (redirect)
- [ ] Browser'da `/yuzdanyuz/admin/login/` → Django admin login sahifasi ko'rinadi
- [ ] CSS/JS yuklanadi (`/yuzdanyuz/static/admin/css/base.css` → 200)
- [ ] `curl https://hsm.sammu.uz/` → Yii2 kasalxona sayti ishlaydi (200, **hech narsa o'zgarmagan**)
- [ ] `curl https://hsm.sammu.uz/yuzdanyuz/api/v1/health/` → Django API endpoint
- [ ] Admin login + form submit ishlaydi (CSRF + session cookies)
- [ ] Static fayllar to'liq yuklanadi (admin sahifasida CSS-li ko'rinadi)
