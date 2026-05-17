# Disaster Recovery Runbook — YuzDanYuz / Milliy Sertifikat

> **Owner**: Backend on-call · **Updated**: 2026-05-17 · **Test cadence**: quarterly drill
>
> Bu hujjat production'da to'qnashilishi mumkin bo'lgan eng yomon stsenariylarni va ularni
> aniqlash + tiklash bo'yicha bosqichma-bosqich harakatlarni yozadi. Drill paytida har 3 oyda
> bir marta sinab ko'ring (oxirgi drill sanasi yuqorida).

---

## 1. PostgreSQL — to'liq backup va tiklash

### Backup strategiyasi
- **Automated `pg_dump` har 6 soatda** — cron task local server'da:
  ```bash
  0 */6 * * *  /home/hsm/.local/pgsql/bin/pg_dump -h 127.0.0.1 -p 5992 -U hsm -d yuzdanyuz_db \
                  | gzip -9 > /home/hsm/backups/postgres/yuzdanyuz_db_$(date +%Y%m%d-%H%M%S).sql.gz
  ```
- **Retention**: oxirgi 10 ta snapshot saqlanadi (`/.claude/commands/db-snapshot.md` skripti
  o'zi rotation qiladi).
- **Off-site copy** (kelajak): WebDAV/S3 yoki Hetzner Storagebox'ga rsync (1 kun retention).

### Tiklash (restore)
```bash
# 1. PostgreSQL ishlayotganligini tasdiqlash
/home/hsm/.local/pgsql/bin/pg_ctl -D /home/hsm/.local/pgsql/data status

# 2. Yangi DB yaratish (DROP qilmasdan — eski'ni saqlash)
/home/hsm/.local/pgsql/bin/createdb -h 127.0.0.1 -p 5992 -U hsm yuzdanyuz_db_restore

# 3. Backup'dan tiklash
gunzip -c /home/hsm/backups/postgres/yuzdanyuz_db_20260517-100000.sql.gz \
  | /home/hsm/.local/pgsql/bin/psql -h 127.0.0.1 -p 5992 -U hsm -d yuzdanyuz_db_restore

# 4. Tasdiqlash
/home/hsm/.local/pgsql/bin/psql -h 127.0.0.1 -p 5992 -U hsm -d yuzdanyuz_db_restore -c \
  "SELECT COUNT(*) FROM accounts_customuser; SELECT COUNT(*) FROM organizations_organization;"

# 5. Switch — Django `DB_NAME=yuzdanyuz_db_restore` ga o'tkazib, gunicorn restart
# Eski DB'ni saqlash: `ALTER DATABASE yuzdanyuz_db RENAME TO yuzdanyuz_db_failed_<ts>;`
```

### RPO/RTO
- **RPO** (Recovery Point Objective): 6 soat (snapshot frequency)
- **RTO** (Recovery Time Objective): 30 daqiqa (manual restore + verify)

---

## 2. Redis — snapshot va tiklash

Redis cache+session+rate-limit+queue ishlatadi. Yo'qotish: barcha login session bekor (foydalanuvchi
qayta login), rate-limit reset (yana 60s logged), Celery queue bo'sh (in-flight task'lar yo'qoladi).

### Backup
- Redis o'zi `dump.rdb` har soatda saqlaydi (default config).
- Manual snapshot: `redis-cli -a foobared123 BGSAVE`.
- Backup location: `/var/lib/redis/dump.rdb` (yoki HestiaCP jail ichida).

### Tiklash
```bash
# 1. Redis to'xtatish
sudo systemctl stop redis-server  # yoki HestiaCP jail'da supervisorctl

# 2. dump.rdb ni almashtirish
cp /home/hsm/backups/redis/dump-20260517.rdb /var/lib/redis/dump.rdb
chown redis:redis /var/lib/redis/dump.rdb

# 3. Redis ishga tushirish
sudo systemctl start redis-server
redis-cli -a foobared123 INFO persistence | grep loading
```

**Acceptable loss**: foydalanuvchilar qayta login qiladi (acceptable). Celery in-flight tasklar — ISSUE-308
DLQ orqali keyin qayta urinilishi mumkin.

---

## 3. HestiaCP jail recovery

### Senariy: jail crash bo'ldi, services down
```bash
# 1. SSH jail'ga kirish
ssh yuzdanyuz@hsm.sammu.uz

# 2. supervisord status
supervisorctl status

# 3. Agar barcha service down — supervisord o'zi restart
supervisorctl reread
supervisorctl update
supervisorctl restart all

# 4. Logs tekshirish
tail -100 /home/yuzdanyuz/private/yuzdanyuz/logs/gunicorn.log
tail -100 /home/yuzdanyuz/private/yuzdanyuz/logs/celery-worker.log
```

### Senariy: `jailbash --die-with-parent` PID 1 o'ldi (eski memory)
- Watchdog (`_yuzdanyuz_keepalive.php` + `watchdog.sh`) avtomatik tiklaydi (60s ichida).
- Manual force: `curl https://hsm.sammu.uz/_yuzdanyuz_keepalive.php` keepalive ping.

---

## 4. Secret rotation (incident response)

### Senariy: `.env` yoki API kalit leak bo'ldi
```bash
# 1. SECRET_KEY rotation
python -c "import secrets; print(secrets.token_urlsafe(50))"
# .env'ga yozish, JWT signing key bilan birga (eski JWT'lar invalid bo'ladi — barcha foydalanuvchi qayta login)

# 2. DB password rotation
sudo -u postgres psql -c "ALTER USER hsm WITH PASSWORD '<new>';"
# .env'da DB_PASSWORD yangilash

# 3. Redis password rotation
redis-cli -a <old> CONFIG SET requirepass <new>
# .env'da REDIS_URL yangilash

# 4. Anthropic API key rotation
# https://console.anthropic.com/settings/keys — yangi key + revoke old
# .env'da ANTHROPIC_API_KEY yangilash

# 5. PlayMobile/Eskiz SMS credentials — provider portal orqali
# .env'da PLAYMOBILE_PASSWORD / ESKIZ_PASSWORD yangilash

# 6. Telegram bot token (agar leak)
# @BotFather → /token → /revoke + yangi
# .env'da TELEGRAM_BOT_TOKEN yangilash

# 7. Gunicorn + Celery restart har bir secret'dan keyin
supervisorctl restart all
```

---

## 5. ALLOWED_HOSTS / production rollback

Production `subpath /yuzdanyuz/` deploy yoki domain o'zgarganda:

```bash
# Eski ALLOWED_HOSTS .env'da:
ALLOWED_HOSTS=.hsm.sammu.uz,127.0.0.1

# Leading-dot syntax (Lesson IELTSmock memory!) — har subdomain qoplaydi
# Agar foydalanuvchi 400/403 ALLOWED_HOSTS xato olsa, yangi domain qo'shish:
ALLOWED_HOSTS=.hsm.sammu.uz,milliysertifikat.uz,127.0.0.1
```

---

## 6. Database migration rollback

Migration apply qilingan, lekin xato (data loss, performance):

```bash
# 1. Oxirgi N migration nomini bilish
./venv/bin/python manage.py showmigrations | tail -20

# 2. Rollback specific migration (masalan catalog'da 0007 dan oldingi 0006'ga qaytish)
./venv/bin/python manage.py migrate catalog 0006_historicalquestion

# 3. Verify: pg_policies / table state
psql ... -c "SELECT relname, relrowsecurity FROM pg_class WHERE relname LIKE 'catalog_%';"
```

**Risk**: yangi migration data backfill qilgan bo'lsa, rollback uchun ataylab `RunPython(reverse=...)`
yozish kerak. Backup'dan tiklash ko'pincha xavfsizroq.

---

## 7. On-call escalation

| Severity | Misol | Aloqa | Response |
|---|---|---|---|
| **P0** | Production down (>5 daqiqa), data leak, security breach | Telegram @narzullayevme | < 15 daqiqa |
| **P1** | Auth tushgan, payment fail, exam start qilolmaydi | Telegram @narzullayevme | < 1 soat |
| **P2** | Sekin response (p95 > 5s), partial feature broken | Email saidmurodnarzullayev@gmail.com | Next business day |
| **P3** | Cosmetic, docs, suggestions | GitHub Issue | Backlog |

### Tashqi vendor aloqalari
- **PlayMobile SMS support**: `support@playmobile.uz`, +998 (71) ...
- **Eskiz support**: `info@eskiz.uz`, dashboard ticket
- **Anthropic status**: https://status.anthropic.com
- **HestiaCP admin** (root access kerak): hosting provider ticket

---

## 8. Drill checklist (har quarter)

- [ ] PostgreSQL backup → restore test (yangi DB'ga, fake user create + login verify)
- [ ] Redis dump.rdb → backup → restore + Celery queue resurrection
- [ ] Secret rotation simulation (test env'da ANTHROPIC_API_KEY rotation + verify)
- [ ] Migration rollback dry-run (1 ta migration reverse)
- [ ] On-call notification path (Telegram bot test message)
- [ ] DR runbook outdated link/command audit

**Last drill**: (drill o'tkazilganda yangilang)

---

## 9. Bog'liq hujjatlar

- `/.claude/commands/db-snapshot.md` — PostgreSQL backup automation
- `docs/pre_launch_checklist.md` — launch'dan oldin DR ready check
- `docs/POSTGRES_SETUP.md` / `POSTGRESQL_PRODUCTION_SETUP.md` — PostgreSQL configuration
- `docs/DEPLOYMENT.md` — production lifecycle umumiy ko'rinishi
- `memory/yuzdanyuz_persistent_backend.md` — HestiaCP jail watchdog pattern
- `memory/yuzdanyuz_production_state.md` — joriy production holati
