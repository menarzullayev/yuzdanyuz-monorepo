# Stabilization Runbook — Pog'ona A

**Sana:** 2026-05-16  
**Maqsad:** Gunicorn crash loop'ni to'xtatish + celery beat DB fix + duplicate nodename fix  
**Deploy QILMAYMIZ** (migration, apache routing keyingi sessiyaga qoldiriladi)  
**Bazaviy hisobot:** [diagnostic-report.md](diagnostic-report.md)  
**Original plan:** [task1.md](task1.md) Step 1-8

---

## ⚠️ Muhim — Bu runbook'ni kim bajaradi?

Sizning standart SSH (`hsm@hsm.sammu.uz`) `jailbash` namespace ichida. Ushbu runbook **bajarish uchun** quyidagilarning biri kerak:

1. **HestiaCP admin SSH (root)** — `ssh root@hsm.sammu.uz` (sizning admin parolingiz bilan)
2. **HestiaCP Web UI** — Files / Web / Cron sahifalarida fayl edit va service restart
3. **Jail-tashqari hsm shell** — agar `bwrap` siz to'g'ridan-to'g'ri kirish yo'li bor bo'lsa

Test: bajarish boshidan tasdiqlash:

```bash
cat /proc/1/cmdline | tr '\0' ' '
# Kutilgan natija jailbash EMAS (`/sbin/init` yoki `systemd` bo'lishi kerak)

ps -ef | grep supervisord | grep -v grep
# Kutilgan natija: kamida 1 ta supervisord PID ko'rinadi
```

Agar ikkalasidan biri jailbash chiqsa yoki ps bo'sh bo'lsa — **STOP**, root access olish kerak.

---

## STEP 0 — Tayyorgarlik (5 daqiqa)

### 0.1 — Hozirgi vaziyat snapshot

```bash
# Backend health (ishlamoqdami?)
curl -s -w "\nHTTP %{http_code} (%{time_total}s)\n" http://127.0.0.1:8001/health/

# Kutilgan: {"status": "ok"} + HTTP 200
# Agar 200 bo'lmasa — supervisord boshqacha holatda. Continue with caution.
```

### 0.2 — Crash loop davom etyaptimi tekshirish

```bash
ls -lh /home/hsm/logs/yuzdanyuz/web.err.log
# Olingan hajmni qayd qil (masalan: 3.6M)
sleep 30
ls -lh /home/hsm/logs/yuzdanyuz/web.err.log
# Agar hajm o'sgan (>50KB farq) — crash loop davom etmoqda
```

### 0.3 — DB backup (har holatda — 2-3 daqiqa)

```bash
mkdir -p /home/hsm/backups
TS=$(date +%Y%m%d_%H%M%S)
/home/hsm/.local/pgsql/bin/pg_dump \
    -h /home/hsm/.local/pgsql/run -p 5992 -U hsm yuzdanyuz_db \
    | gzip > /home/hsm/backups/pre-stabilize-${TS}.sql.gz
ls -lh /home/hsm/backups/pre-stabilize-${TS}.sql.gz

# Kutilgan: ~50-200 MB fayl yaratiladi
# Agar pg_dump fail bo'lsa — postgres ulanish muammosi, STOP
```

**Qayd qiling:** `TS=$TS`, fayl hajmi (rollback uchun).

---

## STEP 1 — Watchdog mexanizmini topish va to'xtatish

### 1.1 — Watchdog jarayonini ko'rish (host namespace)

```bash
pgrep -af "yuzdanyuz-watchdog\|watchdog.sh" 2>&1
ls -la /tmp/yuzdanyuz-watchdog.lock 2>&1
ls -la /tmp/yuzdanyuz-watchdog.flock 2>&1
```

**Kutilgan vaziyatlar:**

- **A.** Watchdog jarayoni mavjud (PID ko'rinadi) — `Step 1.2` ga o'ting
- **B.** Watchdog jarayoni yo'q, lekin `.flock` mavjud — orphan, `Step 1.3` ga o'ting
- **C.** Hech narsa yo'q — watchdog hozir ishlamayapti, PHP-FPM uni keyin spawn qilishi mumkin → `Step 1.4` qildirish kerak

### 1.2 — Watchdog jarayonini sekin to'xtatish

```bash
# Step 1.1'dan PID:
WATCHDOG_PID=<topilgan PID>
kill -TERM $WATCHDOG_PID
sleep 2
ps -p $WATCHDOG_PID 2>&1
# Kutilgan: "no such process"

# Hali tirik bo'lsa:
kill -KILL $WATCHDOG_PID
```

### 1.3 — Lock fayllarni tozalash

```bash
rm -f /tmp/yuzdanyuz-watchdog.lock /tmp/yuzdanyuz-watchdog.flock
ls /tmp/yuzdanyuz-watchdog* 2>&1
# Kutilgan: "No such file"
```

### 1.4 — Watchdog skriptlarini RENAME qilish (PHP-FPM ham qaytadan spawn qilmasligi uchun)

PHP keepalive `/home/hsm/web/hsm.sammu.uz/private/yuzdanyuz/watchdog.sh`'ni ishga tushiradi. Bu ham ikkinchi nusxa `/home/hsm/scripts/yuzdanyuz-watchdog.sh` — ikkalasi bir xil (md5: `9a55c993399351049f1434caf9a7eb0e`).

```bash
mv /home/hsm/web/hsm.sammu.uz/private/yuzdanyuz/watchdog.sh \
   /home/hsm/web/hsm.sammu.uz/private/yuzdanyuz/watchdog.sh.STABILIZE_BAK

mv /home/hsm/scripts/yuzdanyuz-watchdog.sh \
   /home/hsm/scripts/yuzdanyuz-watchdog.sh.STABILIZE_BAK

# Verify:
ls -la /home/hsm/web/hsm.sammu.uz/private/yuzdanyuz/watchdog.sh* 2>&1
ls -la /home/hsm/scripts/yuzdanyuz-watchdog.sh* 2>&1
# Kutilgan: faqat *.STABILIZE_BAK fayllar ko'rinishi kerak
```

**Endi:** PHP keepalive watchdog'ni spawn qilolmaydi (`proc_open` "no such file" qaytaradi). Bu **xavfsiz** — keepalive PHP graceful fail qiladi.

### 1.5 — Verify: 60+ soniya kutib, watchdog qayta tug'ilmadimi

```bash
sleep 70
pgrep -af watchdog 2>&1
ls /tmp/yuzdanyuz-watchdog* 2>&1
# Kutilgan: hech qanday process, hech qanday lock fayl
```

**FAIL:** Agar watchdog qaytadan paydo bo'lsa — boshqa mexanizm bor (cron, systemd). Quyidagilarni tekshiring:

```bash
grep -r "watchdog\|yuzdanyuz" /etc/cron.d/ /etc/cron.daily/ /etc/cron.hourly/ 2>/dev/null
systemctl list-units --all | grep -iE "yuzdanyuz|watchdog"
systemctl list-timers --all 2>&1 | grep -iE "yuzdanyuz|watchdog"
```

---

## STEP 2 — Port 8001'dagi LEGITIMATE gunicorn'ni aniqlash

### 2.1 — Port 8001 egasi

```bash
ss -tlnp 2>&1 | grep ":8001"
# Kutilgan format: users:(("gunicorn",pid=XXXX,fd=Y))
# QAYD QILING: PID = ____  (LEGIT_PID deb ataymiz)

# Alternativa:
lsof -nP -i :8001 2>&1
```

### 2.2 — Gunicorn master + workers

```bash
LEGIT_PID=<yuqoridagi PID>

# Master process:
ps -fp $LEGIT_PID

# Workers (master'ning bolalari):
pgrep -af "gunicorn:.*worker" 2>&1
# yoki:
ps --ppid $LEGIT_PID -f
# Kutilgan: 3 ta worker (supervisord conf: --workers 3)
```

### 2.3 — Boshqa gunicorn jarayonlari (orphan kandidatlar)

```bash
pgrep -af gunicorn 2>&1
# Kutilgan: 1 master + 3 worker + ehtimol bir nechta crash bo'layotgan boshqa master'lar
# Yuqoridagi LEGIT_PID va uning workerlari NORMAL.
# Boshqa "Starting gunicorn" log yozgan masterlar — bu ORPHAN'lar.
```

**Qayd qiling:** LEGIT_PID = `____`, LEGIT_WORKERS PID'lari = `___, ___, ___`

---

## STEP 3 — Orphan gunicorn cleanup

### 3.1 — Avval supervisord'ni TO'XTATING (yangi gunicorn spawn qilmasin)

```bash
# Supervisord PID topish:
SUP_PID=$(pgrep -af "supervisord.*supervisord.conf" | awk '{print $1}')
echo "Supervisord PID: $SUP_PID"

# Sekin to'xtatish — bu BARCHA child'ni TERM qiladi:
kill -TERM $SUP_PID
sleep 5

# Hali tirikmi?
ps -p $SUP_PID 2>&1

# Agar tirik:
kill -KILL $SUP_PID
sleep 2
```

### 3.2 — Yetim gunicorn'larni o'ldirish

```bash
# Endi BARCHA gunicorn jarayonlari yetim — hammasini o'ldirish kerak:
pgrep -af gunicorn
# Bu list'dagi BARCHA PID'larni o'ldirish:
pkill -TERM -f "gunicorn:" 2>&1
sleep 3

# Tirik qolganlar:
pgrep -af gunicorn
# Agar bor — kill -9:
pkill -KILL -f "gunicorn:" 2>&1
sleep 2

# Final tekshir:
pgrep -af gunicorn
# Kutilgan: bo'sh (yoki "no matches")
```

### 3.3 — Daphne (ASGI) ham yetim — tozalash

```bash
pgrep -af daphne 2>&1
pkill -TERM -f "daphne" 2>&1
sleep 2
pkill -KILL -f "daphne" 2>&1
pgrep -af daphne 2>&1
# Kutilgan: bo'sh
```

### 3.4 — Celery worker/beat yetim — tozalash

```bash
pgrep -af "celery -A core" 2>&1
pkill -TERM -f "celery -A core" 2>&1
sleep 5
pkill -KILL -f "celery -A core" 2>&1
pgrep -af celery 2>&1
# Kutilgan: bo'sh
```

### 3.5 — Postgres jarayonini TEGMANG

```bash
# Postgres supervisord boshqaruvida bo'lsa-da, uni qayta yoqish KERAK EMAS.
# Verify postgres hali tirikligini:
pgrep -af postgres 2>&1
# Kutilgan: postmaster + bir nechta worker process

# Postgres haqida xavotirlanmang — supervisord postgres'ni boshqarmaganda ham
# tirik qoladi (chunki postgres o'zi daemon, supervisord faqat exec qildi).
```

### 3.6 — Port 8001 va 8002 bo'sh ekanligini tasdiqlash

```bash
ss -tlnp 2>&1 | grep -E ":8001|:8002"
# Kutilgan: bo'sh — hech kim listen qilmayapti
```

---

## STEP 4 — Celery beat DB connection fix (settings)

### 4.1 — Hozirgi base.py DATABASES bo'limi

`base.py:266-276` quyidagicha:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'yuzdanyuz_db'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '60')),
    }
}
```

`CONN_MAX_AGE` allaqachon bor (default 60), lekin `CONN_HEALTH_CHECKS` yo'q.

### 4.2 — Tuzatish: CONN_HEALTH_CHECKS qo'shish

```bash
# Backup:
cp /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/core/settings/base.py \
   /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/core/settings/base.py.STABILIZE_BAK

# Edit:
sed -i "s|'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '60')),|'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '60')),\n        'CONN_HEALTH_CHECKS': True,|" \
    /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/core/settings/base.py

# Verify:
grep -n -A 1 "CONN_MAX_AGE\|CONN_HEALTH" \
    /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/core/settings/base.py
# Kutilgan:
#   'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '60')),
#   'CONN_HEALTH_CHECKS': True,
```

### 4.3 — Sintaksis tekshirish

```bash
cd /home/hsm/apps/yuzdanyuz-monorepo/packages/backend
venv/bin/python -c "from core.settings import base; print('OK', base.DATABASES['default'].get('CONN_HEALTH_CHECKS'))"
# Kutilgan: "OK True"
```

**FAIL:** Sintaksis xatosi bo'lsa, backup'dan qaytaring: `mv base.py.STABILIZE_BAK base.py`

---

## STEP 5 — Duplicate nodename fix (celery worker conf)

### 5.1 — Hozirgi celery worker conf

`/home/hsm/.config/supervisor/conf.d/yuzdanyuz-celery-worker.conf`:

```ini
command=/home/hsm/apps/yuzdanyuz-monorepo/packages/backend/venv/bin/celery
    -A core worker
    -l info
    -Q default,celery
    --concurrency=2
    -O fair
```

### 5.2 — `-n` flag qo'shish

```bash
# Backup:
cp /home/hsm/.config/supervisor/conf.d/yuzdanyuz-celery-worker.conf \
   /home/hsm/.config/supervisor/conf.d/yuzdanyuz-celery-worker.conf.STABILIZE_BAK

# Edit — '-l info' qatoridan keyin '-n worker1@%h' qo'shamiz:
sed -i 's|    -l info|    -l info\n    -n worker1@%%h|' \
    /home/hsm/.config/supervisor/conf.d/yuzdanyuz-celery-worker.conf

# Verify:
cat /home/hsm/.config/supervisor/conf.d/yuzdanyuz-celery-worker.conf | head -15
# Kutilgan command bo'limi:
#   -A core worker
#   -l info
#   -n worker1@%h
#   -Q default,celery
#   --concurrency=2
```

**E'tibor:** `%%h` ikki marta `%` qo'yiladi (supervisord conf'da `%` escape uchun ikkilantiriladi, celery uchun `%h` = hostname).

**Task1.md aytgan `-Q critical,ai,batch,realtime,celery` haqida diqqat:**
Bu queue'lar Django code'da hali `@task(queue=...)` decorator orqali aniqlanmagan bo'lishi mumkin. Mavjud queue'lar uchun `-Q default,celery` saqlandi. Yangi queue'lar Phase 8 (Celery verification)'ga qoldiriladi. Agar siz haqiqatan ham endi 4 queue ishlatmoqchi bo'lsangiz, alohida tekshirish kerak — bu task scope tashqarisida.

---

## STEP 6 — Supervisord qayta yoqish (toza)

### 6.1 — Eski PID va socket fayllarni tozalash

```bash
rm -f /home/hsm/.local/run/supervisord.pid
rm -f /home/hsm/.local/run/supervisor.sock
rm -f /home/hsm/.local/run/celery-beat.pid
ls /home/hsm/.local/run/
# Kutilgan: bo'sh yoki faqat directory entries
```

### 6.2 — Supervisord daemon ishga tushirish

```bash
# Background daemon (nodaemon=false conf'da o'rnatilgan):
/home/hsm/.local/bin/supervisord -c /home/hsm/.config/supervisor/supervisord.conf

# Yoki agar nodaemon o'zgartirilgan bo'lsa, screen/nohup ichida:
# nohup /home/hsm/.local/bin/supervisord -c /home/hsm/.config/supervisor/supervisord.conf >> /home/hsm/logs/supervisor/supervisord.log 2>&1 &

# Tasdiqlash:
sleep 3
pgrep -af supervisord
# Kutilgan: 1 ta supervisord jarayoni

cat /home/hsm/.local/run/supervisord.pid
# Kutilgan: yangi PID
ls -la /home/hsm/.local/run/supervisor.sock
# Kutilgan: socket fayl yangi yaratilgan
```

### 6.3 — Status verify (15-20 sek kutib)

```bash
sleep 15
/home/hsm/.local/bin/supervisorctl -c /home/hsm/.config/supervisor/supervisord.conf status
```

Kutilgan natija (taxminan):

```
postgres                         RUNNING   pid 12345, uptime 0:00:18
yuzdanyuz-asgi                   RUNNING   pid 12346, uptime 0:00:18
yuzdanyuz-celery-beat            RUNNING   pid 12347, uptime 0:00:13
yuzdanyuz-celery-worker          RUNNING   pid 12348, uptime 0:00:13
yuzdanyuz-web                    RUNNING   pid 12349, uptime 0:00:13
```

**FAIL holatlari:**

| Symptom | Sabab | Tuzatish |
|---|---|---|
| `yuzdanyuz-web` STARTING → BACKOFF | Port 8001 hali ham band (yetim qoldi) | `pkill -KILL -f "gunicorn:"` qaytadan + `supervisorctl restart yuzdanyuz-web` |
| `yuzdanyuz-celery-worker` FATAL | DB hali nostabil yoki nodename xato | celery-worker.err.log tail; `-n worker1@%%h` to'g'ri yozilganini tekshir |
| `postgres` FATAL | postgres allaqachon ishlayotgan bo'lsa "address in use" | `pgrep -af postgres` — agar postgres tirik bo'lsa, supervisord'ning postgres dasturini autostart=false qilish kerak |
| Hammasi RUNNING, lekin uptime restart | Crash loop davom etmoqda | Step 1 watchdog rename'ni qaytadan tekshir |

---

## STEP 7 — Verification (10 daqiqa kuzatish)

### 7.1 — Darhol — birinchi 30 sekund

```bash
# Backend health:
curl -s -w "\nHTTP %{http_code} (%{time_total}s)\n" http://127.0.0.1:8001/health/
# Kutilgan: {"status": "ok"} + HTTP 200

# Process holati:
ps -ef | grep -E "gunicorn|celery|daphne|supervisord" | grep -v grep
# Kutilgan: 1 supervisord + 1 gunicorn master + 3 worker + 1 daphne + 1 celery worker + 1 celery beat
```

### 7.2 — 5 daqiqa kuzatish

```bash
# Crash loop log'ini boshlang'ich hajmda yozib oling:
LOG_SIZE_START=$(stat -c %s /home/hsm/logs/yuzdanyuz/web.err.log)
echo "Start: $LOG_SIZE_START bytes"

# 5 min kutib (300 sek):
sleep 300

LOG_SIZE_END=$(stat -c %s /home/hsm/logs/yuzdanyuz/web.err.log)
echo "End: $LOG_SIZE_END bytes"
echo "Growth: $((LOG_SIZE_END - LOG_SIZE_START)) bytes"

# Kutilgan: growth <10KB (normal log yozish — boot xabarlari + access log)
# Agar growth >100KB — crash loop davom etmoqda, log'larni tekshirish kerak
```

### 7.3 — Logs tekshirish

```bash
# Web (gunicorn):
echo "=== web.err.log so'nggi 20 qator ==="
tail -20 /home/hsm/logs/yuzdanyuz/web.err.log
# Kutilgan: faqat "Booting worker with pid: ..." va "Started server process [N]"
# YO'Q: "Address already in use", "Can't connect"

# Celery beat:
echo "=== celery-beat.err.log so'nggi 20 qator ==="
tail -20 /home/hsm/logs/yuzdanyuz/celery-beat.err.log
# Kutilgan: "beat: Starting...", "Scheduler: Sending due task..."
# YO'Q: "OperationalError: server closed the connection"

# Celery worker:
echo "=== celery-worker.err.log so'nggi 20 qator ==="
tail -20 /home/hsm/logs/yuzdanyuz/celery-worker.err.log
# Kutilgan: "celery@worker1.<hostname> ready"
# YO'Q: "DuplicateNodenameWarning"
```

### 7.4 — Supervisord uptime tasdiqlash

```bash
/home/hsm/.local/bin/supervisorctl -c /home/hsm/.config/supervisor/supervisord.conf status
# Kutilgan: hammasi RUNNING, uptime ~5 daqiqa (300s atrofida)
# AGAR uptime <60s — restart bo'lgan, crash davom etmoqda
```

### 7.5 — Celery worker nodename tasdiqlash

```bash
cd /home/hsm/apps/yuzdanyuz-monorepo/packages/backend
venv/bin/celery -A core inspect active_queues 2>&1 | head -10
venv/bin/celery -A core inspect stats 2>&1 | head -15
# Kutilgan: worker1@<hostname> ko'rinadi (eski celery@<hostname> EMAS)
```

---

## STEP 8 — Final State Report

Quyidagilarni yozib bering (template):

```
STABILIZATION REPORT — Pog'ona A — $(date)
═══════════════════════════════════════

Crash loop sababi: <topilgan mexanizm — masalan "supervisord autorestart + 
                   yetim gunicorn 8001'da">
Vaqtincha to'xtatildi: 
  - /home/hsm/web/hsm.sammu.uz/private/yuzdanyuz/watchdog.sh → *.STABILIZE_BAK
  - /home/hsm/scripts/yuzdanyuz-watchdog.sh → *.STABILIZE_BAK

Servislar holati:
  - supervisord: RUNNING (PID X, uptime Y sec)
  - postgres: RUNNING (PID X, uptime Y sec)
  - yuzdanyuz-web (gunicorn): RUNNING (PID X, uptime Y sec, 3 workers)
  - yuzdanyuz-asgi (daphne): RUNNING (PID X)
  - yuzdanyuz-celery-worker: RUNNING (nodename: worker1@<host>)
  - yuzdanyuz-celery-beat: RUNNING

Fayllar o'zgartirildi:
  - core/settings/base.py (+CONN_HEALTH_CHECKS: True) ← backup: *.STABILIZE_BAK
  - .config/supervisor/conf.d/yuzdanyuz-celery-worker.conf (+ -n worker1@%h)

Logs:
  - web.err.log: 3.6MB → X.X MB (stable, growth <Z KB in 5 min)
  - celery-beat.err.log: oxirgi OperationalError <time> (yoki "no new errors in 5 min")

Health:
  - curl http://127.0.0.1:8001/health/ → HTTP 200 {"status":"ok"}
  - curl celery -A core inspect ping → worker1@<host>: OK pong

Backup:
  - /home/hsm/backups/pre-stabilize-<TS>.sql.gz (XXX MB)

⚠️  KEYINGI SESSIYA UCHUN OCHIQ:
  - Apache routing (Django /admin/, /api/, /health/ public access)
  - 17 ta pending migration (Phase 4 deploy)
  - Watchdog'ni qaytadan yoqish (yangilangan health URL bilan)
  - PHP keepalive endpoint xavfsizligi review
```

---

## ROLLBACK Protocol

Agar Step 4-7 oralig'ida xatolik bo'lsa, eski holatga qaytish:

```bash
# 1. Supervisord'ni to'xtatish:
SUP_PID=$(cat /home/hsm/.local/run/supervisord.pid 2>/dev/null || pgrep -af supervisord | awk '{print $1}')
kill -TERM $SUP_PID
sleep 5
kill -KILL $SUP_PID 2>/dev/null

# 2. Konfiguratsiya fayllarini qaytarish:
mv /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/core/settings/base.py.STABILIZE_BAK \
   /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/core/settings/base.py

mv /home/hsm/.config/supervisor/conf.d/yuzdanyuz-celery-worker.conf.STABILIZE_BAK \
   /home/hsm/.config/supervisor/conf.d/yuzdanyuz-celery-worker.conf

# 3. Watchdog'ni qayta yoqish:
mv /home/hsm/web/hsm.sammu.uz/private/yuzdanyuz/watchdog.sh.STABILIZE_BAK \
   /home/hsm/web/hsm.sammu.uz/private/yuzdanyuz/watchdog.sh

mv /home/hsm/scripts/yuzdanyuz-watchdog.sh.STABILIZE_BAK \
   /home/hsm/scripts/yuzdanyuz-watchdog.sh

# 4. PID/socket tozalash va supervisord qayta start:
rm -f /home/hsm/.local/run/supervisord.pid /home/hsm/.local/run/supervisor.sock
/home/hsm/.local/bin/supervisord -c /home/hsm/.config/supervisor/supervisord.conf

# Eski crash loop holatga qaytasiz, lekin app tirik bo'ladi.
# DB rollback kerak EMAS — biz migration qilmaganmiz.
```

---

## Kerakli komandalar qisqacha (quick reference)

| Maqsad | Komanda |
|---|---|
| Supervisord status | `~/.local/bin/supervisorctl -c ~/.config/supervisor/supervisord.conf status` |
| Service restart | `~/.local/bin/supervisorctl restart yuzdanyuz-web` |
| Conf reload | `~/.local/bin/supervisorctl reread && supervisorctl update` |
| Health check | `curl http://127.0.0.1:8001/health/` |
| Celery inspect | `cd ~/apps/yuzdanyuz-monorepo/packages/backend && venv/bin/celery -A core inspect ping` |
| Logs (live) | `tail -F ~/logs/yuzdanyuz/web.err.log ~/logs/yuzdanyuz/celery-*.err.log` |
| DB backup | `pg_dump -h ~/.local/pgsql/run -p 5992 -U hsm yuzdanyuz_db \| gzip > ~/backups/$(date +%s).sql.gz` |

---

## Tugagandan keyin nima?

1. **Step 8 reportni** yozing va menga yuboring (faqat xulosa bo'limi)
2. Keyingi sessiyada **Pog'ona B** (Apache routing) va **Pog'ona C** (migrations) bilan davom etamiz
3. Frontend tayyor bo'lganda hammasini bir bog'lab apply qilamiz (siz aytganingizdek)
