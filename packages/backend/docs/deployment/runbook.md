# Birinchi production deploy — runbook

Bu hujjat checklist. Batafsil tushuntirish yonidagi hujjatlarda, bu yerda faqat tartib va
tekshiruvlar. Har bandni bajargach belgilang; birorta tekshiruv yiqilsa — to'xtang.

Holat (2026-11-26): kod va Helm chart tayyor, server hali tanlanmagan. Lokal
"prod-repetitsiya" o'tkazildi (pastda), real serverga hech narsa deploy qilinmagan.

## 0. Qarorlar (deploydan oldin, egasi bilan)

- [ ] Qayerga: Kubernetes (Helm chart, `charts/yuzdanyuz`) yoki bitta server + `docker compose`.
      `hsm.sammu.uz` Hestia panelli umumiy server: panel `proc_open` ni bloklaydi va
      supervisord o'chib qoladi, `*.sammu.uz` sertifikati 2026-06-19 da tugagan — u yerga
      faqat yangi sertifikat va Docker bo'lsa.
- [ ] Domen va TLS (Cloudflare proxy, [cloudflare.md](cloudflare.md)).
- [ ] Zaxira manzili (`backup.destination`, [backup.md](backup.md)) — hozircha "keyinroq" deb
      qoldirilgan; prod ma'lumot paydo bo'lishidan oldin hal qilinishi shart.

## 1. Server tayyorlash (faqat o'qish bilan boshlang)

- [ ] Serverdagi eski klonni tekshiring: tarix qayta yozilgan (2026-11-10), eski klonda
      sirlar bor — o'chirishdan oldin nima borligini ko'ring va egasidan tasdiq oling.
- [ ] GHCR image'lari **private**: Helm'da `ghcr-pull` secret, compose'da serverda
      `docker login ghcr.io` (faqat `read:packages` token). Busiz `ImagePullBackOff`.
- [ ] PostgreSQL 16: rollar va RLS — [database-roles.md](database-roles.md) dagi SQL'ni
      **superuser** bajaradi (runtime rol `ALTER ROLE ... SET` qila olmaydi).
      `max_connections`, DB TLS, `pg_trgm` kengaytmasi.
- [ ] Redis parol bilan; tashqariga ochiq emas.

## 2. Sirlar va env ([secrets.md](secrets.md))

`core/settings/prod.py` quyidagilarsiz ishga tushmaydi: `SECRET_KEY`, `DB_PASSWORD`,
`DB_HOST`, `REDIS_URL`, `CELERY_BROKER_URL`, `ALLOWED_HOSTS`, `TELEGRAM_WEBHOOK_SECRET`,
`TRUSTED_PROXY_CIDRS`.

- [ ] `FIELD_ENCRYPTION_KEYS` — **albatta bering**. Bo'sh qolsa kalit `SECRET_KEY` dan
      hosil qilinadi va ishga tushish xato bermaydi; keyin `SECRET_KEY` almashsa AI kalitlari
      va webhook sirlari o'qilmay qoladi.
- [ ] `TRUSTED_PROXY_CIDRS` = ingress/pod CIDR + Cloudflare diapazonlari. Bo'sh bo'lsa
      hamma mijoz bitta IP bo'lib qoladi (rate-limit hammaga birdan ishlaydi).
- [ ] `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS` — frontend domeni.
- [ ] AI: `CLAUDE_CODE_OAUTH_TOKEN` + `CLAUDE_CODE_OAUTH_TOKEN_EXPIRES` (yoki
      `LLM_BACKEND=anthropic_api`). Eski keepalive token public tarixda bo'lgan — yangisini bering.
- [ ] `SENTRY_DSN`; release avtomatik (`GIT_COMMIT_SHA` image ichida).

## 3. Deploy

- [ ] Faqat CI yashil bo'lgan commit'ning o'zgarmas tegi: `image.tag=sha-<40 belgi>`
      (chart `latest` va bo'sh tegni rad etadi).
- [ ] Migratsiyalar alohida job'da (`job-migrate.yaml`), PgBouncer'siz, owner rol bilan.
- [ ] Beat bitta nusxada (single-run lock bor, lekin ikkita beat kerak emas).

## 4. Smoke (deploydan darhol keyin)

```bash
scripts/smoke-deploy.sh https://<domen>
```

Tekshiradi: `/health/`, `/health/ready/` (DB + Redis), HTTP→HTTPS redirect, anonim API
401, HSTS / CSP / Permissions-Policy / X-Frame-Options / nosniff / X-Request-ID.
Keyin qo'lda: login → `/exams` → imtihon boshlash → javob → yakunlash → natija.

## 5. Rollback

- Helm: `helm rollback yuzdanyuz <oldingi revision>`; Argo CD'da oldingi `targetRevision`.
- Migratsiya orqaga qaytmaydigan bo'lsa (data migration) — avval zaxiradan tiklash rejasi.
- Smoke yiqilsa — rollback, keyin tahlil; trafikni ochmang.

## Lokal prod-repetitsiya natijasi (2026-11-26)

`docker compose -p yzr` (postgres 16, pgbouncer, redis, backend, channels, celery-worker,
nginx) + `DJANGO_SETTINGS_MODULE=core.settings.prod` va tasodifiy sirlar:

| Tekshiruv | Natija |
|---|---|
| `/health/`, `/health/ready/` | 200, `database: true, redis: true` |
| HTTP → HTTPS | 301 |
| Xavfsizlik headerlari (`scripts/smoke-deploy.sh`) | hammasi bor |
| Login cookie | `Secure; HttpOnly; SameSite=Lax` |
| Login → ro'yxat → start → 4 javob → submit → natija | 100%, javob javobida `is_correct` yo'q |
| Begona `Origin` bilan POST | 403 |
| Next.js UI (dev, shu backend bilan) | ro'yxat, imtihon, reload'da tiklanish, yakunlash, vaqt tugashi |

Repetitsiyada tekshirilmagan: haqiqiy TLS/ingress, Cloudflare, ko'p pod, zaxira/tiklash,
OpenTelemetry collector.
