═══════════════════════════════════════════════════════════════════════
YuzdanYuz Production Deploy Runbook — Q3+X Batch (28 issues)
Commits: b2518f2 (Sprint 2+3) + 76428a4 (Q3+X)
Date: 2026-05-16
═══════════════════════════════════════════════════════════════════════

Sen production server'dagi local AI agent rolida ishlaysan. Maqsading —
oxirgi 2 ta katta commit'ni production'ga deploy qilish va to'liq verify
qilish. HEC qachon admin access kerak emas — barcha narsa user hsm jail
ichida.

CONTEXT (memory'dan):
- Server: hsm.sammu.uz (HestiaCP jail, user=hsm)
- App path: /home/hsm/apps/yuzdanyuz-monorepo/packages/backend
- venv: /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/venv
- Django port: 8001
- PostgreSQL socket: /home/hsm/.local/pgsql/run, port 5992
- Redis: localhost:6379, password=foobared123
- Process manager: supervisord (PHP-FPM watchdog tutib turadi)
- supervisorctl: /home/hsm/.ok/bin/supervisorctl yoki ~/scripts ichida

KRITIK QOIDA:
- HAR STEP'dan keyin natijani qayd qil (yashil/sariq/qizil)
- Failure bo'lsa keyingi step'ga o'tma — diagnostika qil va to'xta
- Production qarorlar (rollback, restart) uchun user'dan tasdiqlash so'ra
- HAR komandadan oldin kutilgan output'ni bilish — kutilmagan output → STOP

═══════════════════════════════════════════════════════════════════════
PHASE 0 — PRE-FLIGHT (5 daqiqa)
═══════════════════════════════════════════════════════════════════════

Maqsad: deploy oldidan baseline o'rnatish. Hech narsani buzmasdan
hozirgi holatni qayd qilish.

Step 0.1 — Hozirgi commit qayd qilish:
  cd /home/hsm/apps/yuzdanyuz-monorepo/packages/backend
  git log --oneline -1
  
  Kutiladi: 8e06127 fix(backend): ISSUE-103 FK cascade + ISSUE-104/105/106/107
  Agar 76428a4 ko'rinsa → allaqachon deploy qilingan, PHASE 4 dan boshlang.

Step 0.2 — Local o'zgartirishlar yo'qmi:
  git status
  
  Kutiladi: "nothing to commit, working tree clean"
  Agar untracked/modified files bor:
    - Qayd qil (qaysi fayllar) va user'ga xabar ber
    - Stash qilish kerak: git stash push -m "pre-deploy-stash-$(date +%Y%m%d)"
    - Stash list'ni saqla (oxirida pop qilamiz)

Step 0.3 — Supervisord ishlayaptimi:
  supervisorctl status 2>&1 || /home/hsm/.ok/bin/supervisorctl status
  
  Kutiladi: 4-5 process RUNNING (gunicorn, celery_worker, celery_beat, 
            php_watchdog, postgres?)
  Agar BIRORTA process STOPPED/FATAL:
    - tail -50 ~/web/hsm.sammu.uz/private/logs/<service>.log
    - User'ga xabar ber: "X service ishlamayapti — deploy qilishdan oldin 
      tuzatish kerakmi?"

Step 0.4 — DB snapshot (rollback uchun):
  TS=$(date +%Y%m%d_%H%M%S)
  pg_dump -h /home/hsm/.local/pgsql/run -p 5992 -U hsm yuzdanyuz_db \
    | gzip > ~/backups/pre-deploy-${TS}.sql.gz
  ls -lh ~/backups/pre-deploy-${TS}.sql.gz
  
  Kutiladi: fayl yaratildi, hajmi >1MB
  Agar fail: ~/backups directory mavjudligini tekshir (mkdir -p ~/backups)
  Agar pg_dump topilmasa: source ~/.local/pgsql/env yoki PATH'ga qo'sh

Step 0.5 — Disk va memory baseline:
  df -h /home/hsm
  free -h
  
  Disk usage <80% bo'lishi shart (yangi paketlar + historical tables 
  qo'shimcha joy oladi). Agar >85% — PHASE 5 dan oldin disk cleanup kerak.

═══════════════════════════════════════════════════════════════════════
PHASE 1 — GIT PULL (3 daqiqa)
═══════════════════════════════════════════════════════════════════════

Step 1.1 — Origin'dan fetch:
  cd /home/hsm/apps/yuzdanyuz-monorepo
  git fetch origin
  
  Kutiladi: "From github.com:menarzullayev/yuzdanyuz-monorepo
            8e06127..76428a4  main -> origin/main"
  Agar fetch fail: SSH key / network problem, user'ga xabar ber

Step 1.2 — Diff preview (nima kelyapti):
  git log --oneline 8e06127..origin/main
  
  Kutiladi: 2 ta commit ko'rinishi kerak:
    76428a4 feat(backend): Q3 + Cross-cutting batch — 13 issues
    b2518f2 feat(backend): Sprint 2+3 batch — 15 issues

Step 1.3 — Pull:
  git pull --ff-only origin main
  
  Kutiladi: "Updating 8e06127..76428a4
             Fast-forward
             ~80 files changed"
  Agar merge conflict: STOP. User'ga xabar ber, manual aralashuv kerak.

Step 1.4 — Tasdiqlash:
  git log --oneline -3
  
  Kutiladi:
    76428a4 feat(backend): Q3 + Cross-cutting batch
    b2518f2 feat(backend): Sprint 2+3 batch
    8e06127 fix(backend): ISSUE-103 FK cascade

═══════════════════════════════════════════════════════════════════════
PHASE 2 — DEPENDENCY INSTALL (5 daqiqa)
═══════════════════════════════════════════════════════════════════════

Step 2.1 — Venv activate va requirements diff:
  cd /home/hsm/apps/yuzdanyuz-monorepo/packages/backend
  source venv/bin/activate
  
  pip install --dry-run -r requirements/base.txt 2>&1 | grep "Would install"
  
  Kutiladi: yangi paketlar ko'rinishi kerak:
    - django-simple-history (~3.4.x)
    - django-flags (~5.2.x)
    - drf-spectacular (~0.27.x) — agar avval install qilinmagan bo'lsa
    - django-fsm-2 (~4.0.x)
  
  Agar "Would install" qatori yo'q → hammasi up-to-date (skip 2.2)

Step 2.2 — Real install:
  pip install -r requirements/base.txt 2>&1 | tail -20
  
  Kutiladi: "Successfully installed django-simple-history-X.Y.Z 
             django-flags-X.Y.Z ..."
  
  Agar fail (network, compile error):
    - Internet check: curl -s https://pypi.org -o /dev/null -w "%{http_code}\n"
    - Disk: df -h
    - Specific paket fail: pip install <paket>==<version> alohida

Step 2.3 — Install tasdiqlash:
  pip list 2>/dev/null | grep -E "simple-history|django-flags|drf-spectacular|fsm-2"
  
  Kutiladi: 4 qator (yoki 3 — drf-spectacular oldindan bor edi)

═══════════════════════════════════════════════════════════════════════
PHASE 3 — ENV VARS AUDIT (3 daqiqa)
═══════════════════════════════════════════════════════════════════════

Step 3.1 — Hozirgi .env tekshirish:
  cd /home/hsm/apps/yuzdanyuz-monorepo/packages/backend
  cat .env | grep -E "CLICKHOUSE|FEATURE|REWARD|SENTRY" || echo "MISSING"
  
  Kutilishi mumkin missing var'lar:
    - CLICKHOUSE_ENABLED (default: false — OK if missing)
    - CLICKHOUSE_DSN (kerakmas if disabled)
    - REWARD_SERVICE_PATH (default: apps.commerce.wallet_service.award_coins_adapter)
    - LOG_LEVEL (default: INFO)
    - EVENTS_LOG_LEVEL (default: INFO)

Step 3.2 — Yangi env vars qo'shish (faqat ZARUR bo'lsa):
  Hech biri ZARUR EMAS (hammasi default'lar bilan ishlaydi).
  Faqat agar ClickHouse'ga real ulanish kerak bo'lsa CLICKHOUSE_* qo'sh.
  
  Aks holda — bu step'ni o'tkazib yubor.

Step 3.3 — Sintaksis tekshirish:
  python -c "from dotenv import dotenv_values; print(len(dotenv_values('.env')))"
  
  Kutiladi: sonni qaytaradi (15-30 oraliqda), exception emas

═══════════════════════════════════════════════════════════════════════
PHASE 4 — MIGRATIONS (5 daqiqa) — ENG XAVFLI BOSQICH
═══════════════════════════════════════════════════════════════════════

Step 4.1 — Pending migrations ro'yxati:
  python manage.py showmigrations --plan 2>&1 | grep "\[ \]" | head -30
  
  Kutiladi: ~13-15 ta unapplied migration:
    [ ] catalog.0005_question_created_by_question_updated_by
    [ ] catalog.0006_historicalquestion
    [ ] exams.0004_organization_single_device_policy (?)
    [ ] exams.0005_examattempt_created_by_examattempt_updated_by
    [ ] exams.0006_historicalexamattempt
    [ ] analytics.0004_enable_rls
    [ ] analytics.0005_failedtask
    [ ] commerce.0003_enable_rls
    [ ] commerce.0004_organizationsubscription_sub_active_renewable_idx_and_more
    [ ] commerce.0005_organizationsubscription_created_by_and_more
    [ ] commerce.0006_historicalorganizationsubscription_and_more
    [ ] engagement.0003_leaderboardentry
    [ ] organizations.0003_historicalmembership_and_more
    [ ] accounts.0003_historicalcustomuser
    [ ] webhooks.0001_initial
    [ ] flags.0001_initial (django-flags own migration)
  
  Agar SHU LIST'dan biri yo'q — git pull eskirgan, repeat PHASE 1.

Step 4.2 — Migration SQL preview (faqat bitta katta RLS uchun):
  python manage.py sqlmigrate commerce 0003_enable_rls | head -30
  
  Kutiladi: "ALTER TABLE commerce_organizationsubscription ENABLE ROW LEVEL SECURITY;
             CREATE POLICY ..."

Step 4.3 — APPLY (point of no return):
  python manage.py migrate 2>&1 | tee /tmp/migrate-$(date +%Y%m%d_%H%M%S).log
  
  Kutiladi: har migration uchun "OK" (yashil).
  Misol output:
    Operations to perform:
      Apply all migrations: accounts, analytics, ...
    Running migrations:
      Applying catalog.0005... OK
      Applying catalog.0006_historicalquestion... OK
      ...
  
  Agar BIRORTA migration FAIL:
    1. ROLLBACK YO'L: psql -h /home/hsm/.local/pgsql/run -p 5992 -U hsm 
       -d yuzdanyuz_db < /tmp/<old_backup>.sql
    2. User'ga xabar ber + exact xato matnini ber
    3. STOP — keyingi phase'lar'ga o'tma

Step 4.4 — Apply tasdiqlash:
  python manage.py showmigrations --plan 2>&1 | grep "\[ \]" | wc -l
  
  Kutiladi: 0 (hech qanday pending qolmagan)

Step 4.5 — DB schema verify:
  psql -h /home/hsm/.local/pgsql/run -p 5992 -U hsm yuzdanyuz_db -c "
    SELECT count(*) FROM information_schema.tables 
    WHERE table_schema='public' AND table_name LIKE 'historical%';"
  
  Kutiladi: count = 9

═══════════════════════════════════════════════════════════════════════
PHASE 5 — STATIC + DJANGO CHECK (2 daqiqa)
═══════════════════════════════════════════════════════════════════════

Step 5.1 — Static collect (admin'da yangi paketlar uchun):
  python manage.py collectstatic --noinput 2>&1 | tail -5
  
  Kutiladi: "X static files copied to '/path/to/staticfiles', Y unmodified."

Step 5.2 — Django system check:
  python manage.py check --deploy 2>&1
  
  Kutiladi: "System check identified no issues (0 silenced)."
  Yoki: WARNING'lar (SECURE_SSL_REDIRECT, etc.) — production normal,
        critical emas.
  
  Agar ERROR — STOP. Settings.py'da nimadir buzilgan.

═══════════════════════════════════════════════════════════════════════
PHASE 6 — RESTART SERVICES (3 daqiqa)
═══════════════════════════════════════════════════════════════════════

Step 6.1 — Supervisor config'ni yangilash (yangi env yoki path bo'lsa):
  supervisorctl reread
  supervisorctl update
  
  Kutiladi: ko'p hollarda "No config updates" yoki spesifik update'lar

Step 6.2 — Asosiy process'larni restart:
  supervisorctl restart gunicorn celery_worker celery_beat
  
  Kutiladi: har bittasi uchun "stopped" → "started"
  Wait: 5-10 sek

Step 6.3 — Status verify:
  supervisorctl status
  
  Kutiladi: hammasi RUNNING. Uptime 30 sek'dan kam (yangi restart).
  
  Agar BIRORTA STOPPED/FATAL/EXITED:
    tail -50 ~/web/hsm.sammu.uz/private/logs/<service>.log
    Eng tez-tez muammolar:
      - "ModuleNotFoundError: simple_history" → PHASE 2 install bo'lmagan
      - "django.db.utils.OperationalError" → migration noto'g'ri, PHASE 4 ko'r
      - "Permission denied" → fayl egasi noto'g'ri

Step 6.4 — Startup log'larini kuzatish (30 sek):
  timeout 30 tail -f ~/web/hsm.sammu.uz/private/logs/gunicorn-error.log
  
  Kutiladi: "Booting worker with pid: ..." 
           Hech qanday Traceback yo'q.
  Agar Traceback paydo bo'lsa — qayd qil va user'ga xabar ber.

═══════════════════════════════════════════════════════════════════════
PHASE 7 — HTTP ENDPOINT VERIFICATION (5 daqiqa)
═══════════════════════════════════════════════════════════════════════

Asosan curl + status code. Production domain hsm.sammu.uz orqali test
qilamiz (PHP-FPM proxy gunicorn:8001'ga forward qiladi).

Step 7.1 — Health endpoint:
  curl -s -w "HTTP %{http_code} (%{time_total}s)\n" \
    https://hsm.sammu.uz/health/ 
  
  Kutiladi: "HTTP 200 (~0.05s)" + JSON {"status": "ok"}

Step 7.2 — Deep health (DB + Redis + external):
  curl -s "https://hsm.sammu.uz/health/ready/?deep=1" | python -m json.tool
  
  Kutiladi:
    {
      "status": "ok",
      "database": "ok",
      "redis": "ok",
      "anthropic": "ok" yoki "skipped",
      "telegram": "ok",
      ...
    }
  Agar biror servis "fail" — qayd qil. PG/Redis "fail" → kritik, STOP.

Step 7.3 — Yangi OpenAPI schema:
  curl -s -o /dev/null -w "HTTP %{http_code}\n" \
    https://hsm.sammu.uz/api/schema/
  
  Kutiladi: HTTP 200
  
  curl -s -o /dev/null -w "HTTP %{http_code}\n" \
    https://hsm.sammu.uz/api/schema/swagger/
  
  Kutiladi: HTTP 200 (HTML)
  
  curl -s -o /dev/null -w "HTTP %{http_code}\n" \
    https://hsm.sammu.uz/api/schema/redoc/
  
  Kutiladi: HTTP 200

Step 7.4 — /api/v1/ versioned endpoint:
  curl -s -o /dev/null -w "HTTP %{http_code}\n" \
    https://hsm.sammu.uz/api/v1/health/
  
  Kutiladi: HTTP 200 yoki 404 (urls'ga qarab — biz har app uchun v1 mount 
            qilganmiz, lekin /health/ alohida bo'lishi mumkin)

Step 7.5 — Eski /api/ Deprecation header:
  curl -sI https://hsm.sammu.uz/api/exams/ | grep -i "sunset\|deprecation"
  
  Kutiladi: 2 ta header:
    Sunset: <2026-11-16-ish date>
    Deprecation: true

Step 7.6 — Admin sahifalar (anonymous → redirect to login):
  for path in /admin/ /admin/simple_history/historicalquestion/ \
              /admin/flags/flagstate/ /admin/webhooks/webhookendpoint/ \
              /admin/analytics/failedtask/; do
    echo -n "$path: "
    curl -s -o /dev/null -w "HTTP %{http_code}\n" \
      "https://hsm.sammu.uz${path}"
  done
  
  Kutiladi: har biri HTTP 302 (redirect to /admin/login/)
  Agar HTTP 404 — admin URL ro'yxatdan o'tmagan, INSTALLED_APPS check qil.
  Agar HTTP 500 — server xato, gunicorn-error.log tail qil.

═══════════════════════════════════════════════════════════════════════
PHASE 8 — CELERY QUEUE VERIFICATION (3 daqiqa)
═══════════════════════════════════════════════════════════════════════

Step 8.1 — Worker process'lar:
  supervisorctl status | grep celery
  
  Kutiladi: kamida 1 ta celery_worker RUNNING.
  Agar 4 ta alohida worker config qilgan bo'lsangiz:
    celery_worker_critical
    celery_worker_ai
    celery_worker_batch
    celery_worker_realtime
  
  Bizning supervisord conf hozir bir worker (umumiy), Kubernetes deploy'da
  4 worker. Bu OK — production HestiaCP'da single worker, ishlatib turamiz.

Step 8.2 — Active queues (Celery inspect):
  celery -A core inspect active_queues 2>&1 | head -20
  
  Kutiladi: worker name + queue list:
    {'celery_worker_1@hostname': [{'name': 'celery', ...},
                                  {'name': 'critical', ...},
                                  {'name': 'ai', ...},
                                  {'name': 'batch', ...},
                                  {'name': 'realtime', ...}]}
  
  Agar faqat 'celery' default queue ko'rinsa — task_routes hali ishlamayapti,
  worker'ni `-Q critical,ai,batch,realtime,celery` flag bilan ishga 
  tushirish kerak (supervisord conf yangilash).

Step 8.3 — Registered tasks:
  celery -A core inspect registered 2>&1 | grep -E "engagement|exams|analytics|commerce" | head -15
  
  Kutiladi: ~10-15 task qaytadi, har birida to'g'ri name (e.g. 
            "engagement.archive_leaderboards", "exams.finalize_attempt_score").

Step 8.4 — Beat scheduler runs (ko'rib chiqish — actual run kutmaymiz):
  python manage.py shell -c "
  from django_celery_beat.models import PeriodicTask
  for t in PeriodicTask.objects.all():
      print(f'{t.name:40s} last_run={t.last_run_at} enabled={t.enabled}')
  " 2>&1
  
  Kutiladi: ~5-8 scheduled task, hammasi enabled=True.
  Yangi server'da last_run_at=None bo'lishi mumkin — beat boshlanganidan
  keyin tezda yangilanadi.

═══════════════════════════════════════════════════════════════════════
PHASE 9 — POST-DEPLOY OBSERVATION (5 daqiqa kuzatish)
═══════════════════════════════════════════════════════════════════════

Step 9.1 — DLQ jadval (yangi ISSUE-308 — bo'sh bo'lishi shart):
  python manage.py shell -c "
  from apps.analytics.dlq import FailedTask
  count = FailedTask.objects.count()
  recent = FailedTask.objects.order_by('-failed_at')[:5]
  print(f'Total DLQ entries: {count}')
  for t in recent:
      print(f'  {t.failed_at} {t.task_name}: {t.exception[:80]}')
  "
  
  Kutiladi: "Total DLQ entries: 0" (yoki past son — eski test'lardan)

Step 9.2 — 5 daqiqalik error log monitor:
  timeout 300 tail -f ~/web/hsm.sammu.uz/private/logs/gunicorn-error.log \
    ~/web/hsm.sammu.uz/private/logs/celery-worker.log \
    | grep -E "ERROR|CRITICAL|Traceback" &
  
  Wait 5 min. Hech qanday yangi error qator paydo bo'lmasligi kerak.
  Agar error paydo bo'lsa — to'liq stack trace'ni qayd qil.

Step 9.3 — Smoke test (admin login):
  User'dan so'rang: "Admin panel'da /admin/simple_history/, /admin/flags/, 
                     /admin/webhooks/, /admin/analytics/failedtask/ 
                     sahifalarni ochib ko'ring va screenshot bering."

Step 9.4 — Yangi metric:
  curl -s https://hsm.sammu.uz/metrics 2>&1 | grep "yz_task_dlq\|yz_exams_submitted"
  
  Kutiladi: yz_task_dlq_total counter mavjud (yangi ISSUE-308 metric).
  yz_exams_submitted_total mavjud (ISSUE-104 metric, oldin bor edi).

═══════════════════════════════════════════════════════════════════════
PHASE 10 — FINAL REPORT
═══════════════════════════════════════════════════════════════════════

Quyidagi format'da to'liq hisobot ber:

  DEPLOY REPORT — Q3+X Batch — $(date)
  ═══════════════════════════════════════
  
  ✅ Phase 0 Pre-flight: PASS / FAIL (sabab)
  ✅ Phase 1 Git pull: 8e06127 → 76428a4
  ✅ Phase 2 Dependencies: N ta yangi paket installed
  ✅ Phase 3 Env vars: N ta yangi var, qaysilari kerak
  ✅ Phase 4 Migrations: N ta migration applied, M ta historical jadval yaratildi
  ✅ Phase 5 Static + check: OK
  ✅ Phase 6 Restart: gunicorn(uptime), celery(uptime), beat(uptime)
  ✅ Phase 7 HTTP endpoints: 5 ta admin / 3 ta schema / health — barchasi OK
  ✅ Phase 8 Celery: N ta queue active, N ta task registered
  ✅ Phase 9 Observability: DLQ=0, error log=clean, metrics OK
  
  ⚠️  ANOMALIES:
  - (har qanday kutilmagan narsa)
  
  📋 ACTION ITEMS:
  - (user javob talab qiladigan savollar)
  
  🔄 ROLLBACK READY: 
  - Backup: ~/backups/pre-deploy-<TS>.sql.gz (XXX MB)
  - Previous commit: 8e06127
  - Rollback komandasi: 
    git reset --hard 8e06127 && pg_restore ... && supervisorctl restart all

═══════════════════════════════════════════════════════════════════════
ROLLBACK PROTOCOL (faqat critical failure'da)
═══════════════════════════════════════════════════════════════════════

Agar PHASE 4-9 oralig'ida critical failure (>5 endpoint 500 yoki DB corruption):

  1. supervisorctl stop gunicorn celery_worker celery_beat
  2. cd /home/hsm/apps/yuzdanyuz-monorepo/packages/backend
  3. git reset --hard 8e06127
  4. source venv/bin/activate
  5. pip install -r requirements/base.txt   # eski requirements'ga qaytarish
  6. dropdb -h /home/hsm/.local/pgsql/run -p 5992 yuzdanyuz_db
     createdb -h /home/hsm/.local/pgsql/run -p 5992 yuzdanyuz_db
     gunzip -c ~/backups/pre-deploy-<TS>.sql.gz | \
       psql -h /home/hsm/.local/pgsql/run -p 5992 yuzdanyuz_db
  7. supervisorctl start gunicorn celery_worker celery_beat
  8. User'ga xabar: "Rollback complete. Investigate failure cause."

═══════════════════════════════════════════════════════════════════════

Boshlash uchun PHASE 0'dan boshla. Har step'dan keyin natijani qayd qil
va keyingisini boshla. Hech qanday step'da SURPRISE bo'lsa — STOP va 
diagnostika.
