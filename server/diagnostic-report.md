# Diagnostic Report — hsm.sammu.uz

**Sana:** 2026-05-16 17:20+05  
**Bajaruvchi:** Local AI agent (nsn-pc) → SSH → hsm@srvr1.sammu.uz  
**Asos:** [task1.md](task1.md) PHASE 0 pre-flight bajarib turib aniqlangan anomaliyalar  
**Holat:** ⛔ **Deploy boshlanmagan** — server degraded state'da, oldin barqarorlashtirish kerak

---

## 1. Executive Summary

Task1.md production deploy uchun yozilgan edi, lekin pre-flight tekshiruvi davomida server **degraded operational state**'da ekanligi aniqlandi. Joriy commit allaqachon target `76428a4`, lekin **17 ta migration pending**, gunicorn **crash loop**'da va supervisord daemon **o'lik**.

Migration'larni hozir apply qilish — DB connection nostabilligi va process management uzilganligi sababli — **xavfli**. Avval quyidagi 4 ta ildiz muammosini hal qilish kerak:

1. Gunicorn crash loop (har 5-6 sek yangi PID, port 8001 band)
2. Supervisord daemon o'lik (PID file orphan)
3. Apache/nginx Django'ga route qilmaydi (public endpoint 404)
4. Celery beat DB connection drop

---

## 2. Tekshirilgan muhit

| Parametr | Qiymat |
|---|---|
| Server (FQDN) | `srvr1.sammu.uz` (public domain `hsm.sammu.uz` → `109.94.172.117`) |
| Foydalanuvchi | `hsm` (HestiaCP jail) |
| App path | `/home/hsm/apps/yuzdanyuz-monorepo/packages/backend` |
| Venv | `/home/hsm/apps/yuzdanyuz-monorepo/packages/backend/venv` (Python 3.11.2) |
| PostgreSQL | socket `/home/hsm/.local/pgsql/run`, port `5992`, DB `yuzdanyuz_db` |
| Django port | `127.0.0.1:8001` (gunicorn) |
| ASGI port | `127.0.0.1:8002` (daphne, ehtimol) |
| Disk | 80% used (`/dev/sda1` 6 TB / 4.5 TB used / 1.2 TB free) |
| RAM | 984 GB total, 458 GB available |
| Supervisord conf | `/home/hsm/.config/supervisor/supervisord.conf` |
| Supervisord socket | `/home/hsm/.local/run/supervisor.sock` (refused connection) |
| Logs (haqiqiy yo'l) | `~/logs/yuzdanyuz/` va `~/logs/supervisor/` *(task1.md noto'g'ri yo'l ko'rsatgan: `~/web/hsm.sammu.uz/private/logs/`)* |

---

## 3. Kritik topilmalar (raqamli ko'rsatkichlar bilan)

### 3.1 🔴 Gunicorn crash loop

`~/logs/yuzdanyuz/web.err.log` (3.4 MB, doimiy o'sib bormoqda):

```
[2026-05-16 17:20:47] [PID 2386337] [INFO] Starting gunicorn 23.0.0
[2026-05-16 17:20:47] [ERROR] Connection in use: ('127.0.0.1', 8001)
[2026-05-16 17:20:48] [ERROR] Connection in use: ('127.0.0.1', 8001)
[2026-05-16 17:20:49] [ERROR] Connection in use: ('127.0.0.1', 8001)
[2026-05-16 17:20:52] [PID 2386350] [INFO] Starting gunicorn 23.0.0   ← yangi PID
[2026-05-16 17:20:59] [PID 2386366] [INFO] Starting gunicorn 23.0.0   ← yangi PID
[2026-05-16 17:21:05] [PID 2386848] [INFO] Starting gunicorn 23.0.0   ← yangi PID
```

**Mexanizm:** Har 5-7 sekundda yangi `gunicorn` jarayoni boshlanadi, port band ekanini ko'rib 5-6 marta urinib ko'radi, keyin chiqib ketadi. Eski (ishlayotgan) gunicorn ham hali joyida. Tipik watchdog-restart loop.

**Mumkin sabablari (tekshirish kerak):**
- `~/scripts/yuzdanyuz-watchdog.sh` cron orqali ishlatilyaptimi? (`crontab -l` bo'sh ko'rindi, lekin systemd timer yoki boshqa joyda bo'lishi mumkin)
- `~/scripts/start-all.sh` boshqa jarayon ichida cycle'da ishlamoqdami?
- supervisord avtorestart parameter'i bilan, lekin orphan state'da?

**Diagnostika komandalari:**
```bash
ssh -i ~/.ssh/ssh_key_hsm hsm@hsm.sammu.uz
# Crontab + systemd timers tekshirish
crontab -l
systemctl --user list-timers 2>/dev/null
systemctl list-timers 2>/dev/null | grep -i yuzdanyuz

# Kim gunicorn restartni boshlayapti — parent PPID kuzatish
while true; do ps -ef | grep gunicorn | grep -v grep | head -3; echo "---"; sleep 2; done

# Faylda nima referenslar bor
grep -r "gunicorn" ~/scripts/ ~/.config/supervisor/conf.d/ 2>/dev/null
```

### 3.2 🔴 Supervisord daemon o'lik

```
/home/hsm/.local/run/supervisord.pid → 211
/proc/211/cmdline                    → No such file or directory
unix:///home/hsm/.local/run/supervisor.sock → refused connection
```

**Tahlil:** Supervisord crash bo'lib, PID file qoldirilgan. Hozir ishlayotgan barcha service'lar (gunicorn, celery_worker, postgres) **yetim** — onasi yo'q, lekin hali ham log yozyapti (`supervisord.log` 858KB, `~/logs/supervisor/postgres.err.log` 1.1MB). Bu mantiqsiz, demak yoki:
- Supervisord crash juda yaqinda sodir bo'lgan
- YOKI ikkita supervisord misli mavjud edi, biri o'ldi
- YOKI watchdog script supervisord'ni ham qayta-qayta tiklayapti

**Tuzatish (ehtiyot bilan):**
```bash
# 1. Avval barcha mavjud yetim jarayonlarni topish
ps auxf | grep -E "gunicorn|celery|daphne|supervisord" | grep -v grep

# 2. PID file'ni tozalash
rm /home/hsm/.local/run/supervisord.pid

# 3. Yetim gunicorn'larni o'ldirish (faqat eski PID'lar — yangilarini emas!)
# E'TIBOR: avval qaysi gunicorn ishlayotganini va 8001'ga bog'langanini aniqlash kerak

# 4. Supervisord qayta start
~/.local/bin/supervisord -c ~/.config/supervisor/supervisord.conf
# yoki
/home/hsm/.ok/bin/supervisord -c ~/.config/supervisor/supervisord.conf

# 5. Status tekshirish
supervisorctl -c ~/.config/supervisor/supervisord.conf status
```

### 3.3 🔴 Apache/nginx Django'ga route qilmaydi

Public endpoint test natijasi:

| URL | HTTP Code | Javob beruvchi |
|---|---|---|
| `https://hsm.sammu.uz/` | 200 | Yii2 PHP (cookie: `advanced-frontend`, `_csrf-frontend`) |
| `https://hsm.sammu.uz/health/` | **404** | nginx (Yii2 fallback) |
| `https://hsm.sammu.uz/api/v1/health/` | **404** | nginx |
| `https://hsm.sammu.uz/admin/` | **404** | nginx |
| `https://hsm.sammu.uz/api/schema/` | **404** | nginx |

**Sabab:** `/home/hsm/conf/web/hsm.sammu.uz/apache2.conf` (HestiaCP shabloni):

```apache
<VirtualHost 109.94.172.117:8080>
    ServerName hsm.sammu.uz
    DocumentRoot /home/hsm/web/hsm.sammu.uz/public_html
    # Faqat PHP-FPM Yii2 uchun ProxyPass yo'q!
    <FilesMatch \.php$>
        SetHandler "proxy:unix:/run/php/php8.4-fpm-hsm.sammu.uz.sock|fcgi://localhost"
    </FilesMatch>
</VirtualHost>
```

`ProxyPass /admin/ http://127.0.0.1:8001/admin/` yoki shunga o'xshash Django uchun routing **yo'q**. Public traffic faqat Yii2'ga boryapti.

**Tuzatish variantlari:**

**A.** HestiaCP qo'shimcha config orqali (avtomatik yo'q bo'lib ketmaydi):
```bash
# Yangi fayl yaratish (apache2.conf_* pattern bilan IncludeOptional ulanadi):
cat > /home/hsm/conf/web/hsm.sammu.uz/apache2.conf_django <<'EOF'
ProxyPreserveHost On
ProxyPass /admin/ http://127.0.0.1:8001/admin/
ProxyPassReverse /admin/ http://127.0.0.1:8001/admin/
ProxyPass /api/ http://127.0.0.1:8001/api/
ProxyPassReverse /api/ http://127.0.0.1:8001/api/
ProxyPass /health/ http://127.0.0.1:8001/health/
ProxyPassReverse /health/ http://127.0.0.1:8001/health/
ProxyPass /metrics http://127.0.0.1:8001/metrics
ProxyPassReverse /metrics http://127.0.0.1:8001/metrics
EOF
# HestiaCP'da web restart kerak (HestiaCP admin orqali yoki v-restart-web command)
```

**B.** Django'ni alohida subdomain'da (`api.hsm.sammu.uz`) ishga tushirish — toza yechim, lekin DNS + nginx config talab qiladi.

### 3.4 🟡 Celery beat DB connection drop

`~/logs/yuzdanyuz/celery-beat.err.log` so'nggi traceback:

```
File "django/db/backends/utils.py", line 105, in _execute
    return self.cursor.execute(sql, params)
django.db.utils.OperationalError: server closed the connection unexpectedly
    This probably means the server terminated abnormally
    before or while processing the request.
```

**Mumkin sabablari:**
- PostgreSQL TCP keepalive sozlanmagan, idle connection drop bo'ladi
- `CELERY_BEAT_SCHEDULER` django-celery-beat ishlatadi, har minutga DB query yuboradi
- PgBouncer yo'q, doimiy connection ishlatadi
- Tarmoq beqarorligi (lekin unix socket ishlatilgani uchun bu mumkin emas)

**Tuzatish:** Django settings'ga `CONN_MAX_AGE = 60` yoki `0` (har queryda yangi connection) qo'shish, yoki `CONN_HEALTH_CHECKS = True` (Django 4.1+).

### 3.5 🟡 Celery worker DuplicateNodenameWarning

```
DuplicateNodenameWarning: Received multiple replies from node name: celery@srvr1.sammu.uz.
```

Ikkita celery worker bir xil nodename bilan ishlayapti. Restart vaqtida orphan jarayon to'g'ri tozalanmagan. Production'da har worker uchun `-n` flag bilan unique nodename berish kerak.

### 3.6 🟢 Pending migrations (deploy uchun barbod)

Joriy commit allaqachon `76428a4`, lekin migration'lar **apply qilinmagan** (`git pull` orqali kod kelgan, `migrate` esa yo'q):

```
[ ] accounts.0003_historicalcustomuser
[ ] analytics.0004_enable_rls                          ← RLS — POSTGRES policy
[ ] analytics.0005_failedtask                          ← DLQ jadval
[ ] organizations.0003_historicalmembership_historicalorganization_and_more
[ ] catalog.0005_question_created_by_question_updated_by
[ ] catalog.0006_historicalquestion                    ← simple-history
[ ] commerce.0003_enable_rls                           ← RLS
[ ] commerce.0004_organizationsubscription_sub_active_renewable_idx_and_more
[ ] commerce.0005_organizationsubscription_created_by_and_more
[ ] commerce.0006_historicalorganizationsubscription_and_more
[ ] engagement.0003_leaderboardentry
[ ] exams.0005_examattempt_created_by_examattempt_updated_by
[ ] exams.0006_historicalexamattempt
[ ] flags.0012_replace_migrations_for_wagtail_independence  ← (11 squashed)
[ ] flags.0013_add_required_field
[ ] flags.0014_flagstate_unique_constraint
[ ] webhooks.0001_initial
```

Jami: **17 ta pending** (task1.md 13-15 kutgan edi — flags 3ta squash bilan qo'shimcha).  
Applied: **92 ta** (DB tirik, oldin migrate ishlagan).

**Diqqat:** `analytics.0004_enable_rls` va `commerce.0003_enable_rls` PostgreSQL **Row Level Security** ni yoqadi — kritik xavfsizlik o'zgarishi, rollback murakkab.

### 3.7 🟢 Backend ichki sathda 200 qaytaryapti

Bu yagona ijobiy belgi:

```bash
$ curl http://127.0.0.1:8001/health/
{"status": "ok"}
HTTP/1.1 200 OK
Server: gunicorn
```

Bitta gunicorn jarayoni 8001'ga bog'langan va Django app `/health/` endpoint'ini qaytaryapti. Ya'ni eski commit'larning kodi to'liq ishlayapti (yangi migration'lar talab qilmaydigan endpoint'lar uchun).

---

## 4. Task1.md noto'g'riligi joylashgan joylar

| Bo'lim | Task1.md aytadi | Haqiqat |
|---|---|---|
| Logs yo'l | `~/web/hsm.sammu.uz/private/logs/` | `~/logs/yuzdanyuz/` va `~/logs/supervisor/` |
| Pending count | ~13-15 | **17** (flags qo'shimcha 3 ta) |
| Process'lar holati | RUNNING | gunicorn ✅ (lekin crash loop), celery worker ✅, beat ⚠️ DB error, asgi ❌ port band |
| Public endpoint | HTTP 200 JSON | HTTP 404 — Django route qilinmagan |
| supervisorctl | Ishlaydi | Refused connection (daemon o'lik) |

---

## 5. Tavsiya etilgan harakatlar tartibi

**Quyidagi tartibni qat'iy bajarish kerak — qadamni o'tkazib yuborish DB corruption riski.**

### Pog'ona A — Tezkor barqarorlashtirish (deploy emas, faqat yashash)

1. **Watchdog/cron'ni topib to'xtatish** (gunicorn crash loop sababi)  
   ```bash
   # diagnostika:
   grep -r "gunicorn\|yuzdanyuz" /etc/cron.d/ /etc/cron.hourly/ 2>/dev/null
   systemctl list-timers 2>/dev/null
   pgrep -af watchdog
   ```

2. **Yetim gunicorn jarayonlarini tozalash** (eski ishlayotganini saqlab)  
   ```bash
   # 8001'ga bog'langan PID'ni topish:
   ss -tlnp 2>&1 | grep 8001  # yoki sudo bilan kerak
   # Boshqa gunicorn'larni hech bir port'ga bog'lanmagan holida o'ldirish
   ```

3. **Supervisord'ni qayta yoqish** (PID file ham tozalab)  
   ```bash
   rm /home/hsm/.local/run/supervisord.pid
   ~/.local/bin/supervisord -c ~/.config/supervisor/supervisord.conf
   supervisorctl status
   ```

4. **Celery beat DB issue uchun temporary fix** — Django settings.py'ga:  
   ```python
   DATABASES["default"]["CONN_MAX_AGE"] = 0
   DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
   ```

### Pog'ona B — Routing (public uchun)

5. **Apache config'ga Django ProxyPass qo'shish** (3.3-bo'limdagi A variant)
6. **Restart Apache** (HestiaCP orqali yoki sudo bilan)
7. **Verify:** `curl https://hsm.sammu.uz/health/` → 200 kutiladi

### Pog'ona C — Deploy (asl task1.md PHASE 4-9)

8. **DB backup** (task1.md Step 0.4):  
   ```bash
   TS=$(date +%Y%m%d_%H%M%S)
   mkdir -p ~/backups
   /home/hsm/.local/pgsql/bin/pg_dump -h /home/hsm/.local/pgsql/run -p 5992 \
     -U hsm yuzdanyuz_db | gzip > ~/backups/pre-deploy-${TS}.sql.gz
   ls -lh ~/backups/pre-deploy-${TS}.sql.gz
   ```

9. **PHASE 2** — Dependencies (`pip install -r requirements/base.txt`) — task1.md aytganidek
10. **PHASE 4** — Migration'larni **bosqichma-bosqich** apply qilish:
    ```bash
    # Avval kichik fixed-schema migration'lar:
    venv/bin/python manage.py migrate accounts 0003
    venv/bin/python manage.py migrate organizations 0003
    venv/bin/python manage.py migrate catalog 0005
    venv/bin/python manage.py migrate catalog 0006

    # Keyin RLS migration'lar (ENG XAVFLI — alohida verify):
    venv/bin/python manage.py sqlmigrate analytics 0004_enable_rls | less
    venv/bin/python manage.py migrate analytics 0004
    venv/bin/python manage.py migrate analytics 0005
    venv/bin/python manage.py sqlmigrate commerce 0003_enable_rls | less
    venv/bin/python manage.py migrate commerce 0003

    # Qolganlari:
    venv/bin/python manage.py migrate
    ```

11. **PHASE 5-9** — task1.md aytganidek, faqat log yo'llari va supervisorctl xato joylarini tuzatib.

---

## 6. Rollback tayyorgarlik (har holatda)

Hozir backup mavjud emas. Hech narsa qilinmagan bo'lsa ham, oldindan backup yarating:

```bash
ssh -i ~/.ssh/ssh_key_hsm hsm@hsm.sammu.uz
mkdir -p ~/backups
TS=$(date +%Y%m%d_%H%M%S)
/home/hsm/.local/pgsql/bin/pg_dump -h /home/hsm/.local/pgsql/run -p 5992 \
  -U hsm yuzdanyuz_db | gzip > ~/backups/pre-deploy-${TS}.sql.gz
ls -lh ~/backups/pre-deploy-${TS}.sql.gz
```

Restore (agar kerak bo'lsa):
```bash
supervisorctl stop all
/home/hsm/.local/pgsql/bin/dropdb -h /home/hsm/.local/pgsql/run -p 5992 -U hsm yuzdanyuz_db
/home/hsm/.local/pgsql/bin/createdb -h /home/hsm/.local/pgsql/run -p 5992 -U hsm yuzdanyuz_db
gunzip -c ~/backups/pre-deploy-${TS}.sql.gz | \
  /home/hsm/.local/pgsql/bin/psql -h /home/hsm/.local/pgsql/run -p 5992 -U hsm yuzdanyuz_db
supervisorctl start all
```

---

## 7. Mening men yuritgan komandalar (audit trail)

Diagnostika davomida quyidagilar **faqat o'qish** rejimida ishlatilgan, hech qanday o'zgarish kiritmadim:

- `git log --oneline -1`, `git status` — repository inspect
- `supervisorctl status` (failed)
- `ps -ef | grep ...`, `ss -tlnp`, `crontab -l`
- `df -h`, `free -h`
- `curl http://127.0.0.1:8001/health/` (and other paths)
- `venv/bin/python manage.py showmigrations`
- `psql -c "SELECT count(*) FROM information_schema.tables ..."`
- `tail` log files
- `cat` config files (`nginx.conf`, `apache2.conf`, `supervisord.conf`)
- `ls` various directories

**Hech qanday yozish operatsiyasi yo'q.** DB backup ham yaratilmagan (task1.md Step 0.4 bajarilmagan).

---

## 8. Aloqa va kontekst

Agar siz qaror qilsangiz men keyingi qadamlarni davom ettirsin (Pog'ona A, B yoki C), shu fayldan faza tanlangani va meni ishga tushiring. Har biri uchun avval tasdiq so'rayman.

**File path:** [server/diagnostic-report.md](diagnostic-report.md) (siz hozir o'qiyotgan fayl)  
**Task source:** [server/task1.md](task1.md)
