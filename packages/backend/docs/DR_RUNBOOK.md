# Disaster Recovery Runbook — YuzDanYuz / Milliy Sertifikat

> **Owner**: Backend on-call · **Updated**: 2026-11-12 · **Drill**: har chorakda (pastdagi jurnal)
>
> Joriy production dizayni uchun: Kubernetes + Helm chart (`charts/yuzdanyuz`), tashqi
> PostgreSQL 16 (RLS), PgBouncer (transaction pooling), Redis, RWX media PVC. Eski
> HestiaCP/supervisord tartibi bekor qilingan va bu yerda yo'q.
>
> Batafsil backup/restore buyruqlari: [`docs/operations/backup-restore.md`](../../../docs/operations/backup-restore.md)
> (repo ildizida). Bu hujjat — qaysi holatda nima qilinadi va qancha vaqt/ma'lumot
> yo'qotish kutiladi.

---

## 0. Maqsadlar (RPO / RTO)

| Komponent | Backup | RPO (yo'qotish) | RTO (tiklash) | Izoh |
|---|---|---|---|---|
| PostgreSQL | `CronJob <release>-backup`: kunlik `pg_dump -Fc` → GPG → rclone (03:00 UTC, 30 kun) | **≤ 24 soat** | **≤ 2 soat** | Restore 2026-11-12 da mashq qilindi. 24 soatdan kam RPO kerak bo'lsa — WAL arxivlash / provayder PITR (quyida "Ma'lum bo'shliqlar") |
| Media PVC (`<release>-media`) | **chart backup qilmaydi** | storage class snapshot'iga bog'liq | — | Ma'lum bo'shliq; foydalanuvchi fayllari va eksportlar |
| Redis | yo'q (ataylab) | hammasi | ~5 daqiqa (yangi bo'sh Redis) | Kesh, tokenlar, rate limit, Celery navbati, Channels |
| Kubernetes resurslari | git (`charts/`, `argocd/`) + offline secret'lar | 0 | ≤ 1 soat | `helm upgrade --install` qayta quradi |

RTO 2 soat: dump yuklash + `sha256` + GPG decrypt + `pg_restore -j 4` + tekshiruv +
cutover. 2026-11-12 dagi dump 0,5 MB edi; baza o'sgan sari `pg_restore` vaqtini har
drill'da qayta o'lchang va jadvalni yangilang.

---

## 1. PostgreSQL ma'lumotlari yo'qoldi yoki buzildi

Belgilar: 5xx ko'payadi, `relation ... does not exist`, noto'g'ri/yo'qolgan yozuvlar,
provayder "volume lost" xabari.

1. **Yozuvchilarni to'xtating** (yangi buzilish va split-brain bo'lmasin):
   ```bash
   kubectl -n <ns> scale deploy -l app.kubernetes.io/instance=<release> --replicas=0
   kubectl -n <ns> patch cronjob <release>-backup -p '{"spec":{"suspend":true}}'
   ```
   PgBouncer ham to'xtaydi — bu to'g'ri: eski bazaga ochiq server ulanishlari qolmaydi.
2. **Tiklang** — `backup-restore.md` "Restore" bo'limi, 1–5 qadamlar: dump tanlash,
   `sha256sum -c`, offline kalit bilan decrypt, **yangi** `yuzdanyuz_restore` bazasiga
   `pg_restore --exit-on-error`, sonlarni tekshirish (`django_migrations`,
   `accounts_customuser`, `relrowsecurity` ≥ 17).
   Restore to'g'ridan-to'g'ri Postgres'ga qilinadi, PgBouncer orqali emas.
3. **Cutover**: bazalarni almashtiring (`ALTER DATABASE ... RENAME`) yoki
   `externalServices.postgres.name` ni o'zgartiring, keyin:
   ```bash
   helm upgrade yuzdanyuz charts/yuzdanyuz -f values.yaml -f values-prod.yaml <prod --set'lar>
   kubectl -n <ns> patch cronjob <release>-backup -p '{"spec":{"suspend":false}}'
   ```
   `helm upgrade` migrate Job'ini ham ishga tushiradi (restore'dan keyingi migratsiyalar).
4. **Keyin**: backup vaqtidan keyingi ma'lumot yo'qolgan (RPO). To'lov provayderlari
   (Payme/Click) kabinetidan shu oraliqdagi tranzaksiyalarni solishtiring.

## 2. PostgreSQL primary ishlamayapti (failover)

Boshqariladigan Postgres failover qilsa, host nomi o'sha qoladi, IP o'zgaradi.

- PgBouncer serverga qayta ulanadi (`server_login_retry = 5`), lekin DNS keshini
  (`dns_max_ttl`, standart 15 s) kutadi. 1 daqiqadan keyin ham xato bo'lsa:
  `kubectl -n <ns> rollout restart deploy/<release>-pgbouncer`.
- Failover paytidagi so'rovlar xato beradi (ilova `query_wait_timeout = 15 s` dan keyin
  javob qaytaradi, osilib qolmaydi). Celery vazifalari `acks_late` bilan navbatga
  qaytadi; `finalize_attempt_score` DB xatosida o'zi qayta urinadi.

## 3. PgBouncer muammolari

- `no more connections allowed (max_client_conn)` → `pgbouncer.maxClientConn` ni oshiring.
- `query_wait_timeout` xatolari (ulanish kutish) → Postgres'da sekin so'rovlar bormi
  (`pg_stat_activity`), yoki `pgbouncer.maxDbConnections` × replicas yetarli emasmi.
  Chart `replicas × (maxDbConnections + workerMaxDbConnections)` ni
  `maxConnections − reservedConnections` dan oshirishga yo'l qo'ymaydi.
- Favqulodda chetlab o'tish (faqat qisqa muddat): `--set pgbouncer.enabled=false` —
  ilova to'g'ridan-to'g'ri Postgres'ga ulanadi, lekin HPA maksimumida ulanishlar
  `max_connections` dan oshib ketishi mumkin.

## 4. Redis yo'qoldi

Redis'da faqat tiklanadigan holat bor; backup qilinmaydi.

Yo'qotish oqibati: barcha foydalanuvchilar qayta login qiladi, rate-limit hisoblagichlari
nolga tushadi, Django kesh bo'sh, Channels guruhlari uziladi (WebSocket'lar qayta
ulanadi), **Celery navbatidagi vazifalar yo'qoladi**.

1. Yangi Redis'ni ishga tushiring (bir xil URL/parol, `REDIS_URL`, `CELERY_BROKER_URL`,
   `CHANNELS_REDIS_URL` secret'lari).
2. `kubectl -n <ns> rollout restart deploy -l app.kubernetes.io/instance=<release>`.
3. Davriy vazifalar o'z jadvalida qayta ishlaydi (hammasi `core/celery.py`
   `beat_schedule` da, idempotent). Topshirilgan, lekin balli hisoblanmagan imtihonlarni
   qayta navbatga qo'ying:
   ```bash
   kubectl -n <ns> exec deploy/<release>-web -- python manage.py shell -c "
   from apps.exams.models import ExamAttempt; from apps.exams.tasks import finalize_attempt_score
   from core.tenant import unscoped_context
   with unscoped_context():
       for pk in ExamAttempt.objects.filter(status='submitted', submitted_at__gte='<Redis yo\'qolgan vaqt>').values_list('pk', flat=True):
           finalize_attempt_score.delay(str(pk))"
   ```

## 5. Klaster yoki namespace yo'qoldi

1. Yangi klasterda: ingress-nginx, cert-manager, (ixtiyoriy) KEDA, Prometheus operator.
2. Secret'larni offline manbadan tiklang: `yuzdanyuz-secrets`, `ghcr-pull`,
   `yuzdanyuz-backup-rclone` (`docs/deployment/secrets.md`).
3. `helm upgrade --install` (prod `--set`'lar bilan, `backup-restore.md` "Setting it up" 3).
4. Postgres ham yo'qolgan bo'lsa — 1-bo'lim. Media PVC bo'sh bo'ladi (backup yo'q).

## 6. Yomon deploy / migratsiya

```bash
helm -n <ns> history yuzdanyuz
helm -n <ns> rollback yuzdanyuz <revision>
```

`helm rollback` kod va konfiguratsiyani qaytaradi, **migratsiyalarni qaytarmaydi**.
Sxema o'zgargan bo'lsa: `python manage.py migrate <app> <oldingi_migratsiya>`
(`RunPython` teskari funksiyasi bo'lsagina xavfsiz) yoki 1-bo'lim bo'yicha backup'dan
tiklash. `CREATE INDEX CONCURRENTLY` migratsiyalari (`catalog 0012`) yarim qolsa,
`INVALID` indeksni `DROP INDEX CONCURRENTLY` qilib migratsiyani qayta ishga tushiring.

## 7. Secret rotation (sizib chiqish)

Secret'ni `yuzdanyuz-secrets` da yangilang, keyin barcha podlarni qayta ishga tushiring:

```bash
kubectl -n <ns> create secret generic yuzdanyuz-secrets --from-env-file=<yangi .env> \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n <ns> rollout restart deploy -l app.kubernetes.io/instance=<release>
```

- `SECRET_KEY`: barcha JWT/sessiyalar bekor bo'ladi (qayta login).
- `DB_PASSWORD`: avval Postgres'da `ALTER ROLE ... PASSWORD`, so'ng secret, so'ng
  restart (PgBouncer userlist'ni ishga tushishda secret'dan yozadi).
- Redis paroli: Redis'da `CONFIG SET requirepass`, so'ng uchala Redis URL secret'i.
- Anthropic / Claude token, Telegram bot token, SMS va to'lov kalitlari: provayder
  kabinetida yangisini chiqarib, eskisini bekor qiling.
- Backup GPG kaliti: yangi juft, yangi public key bilan `helm upgrade`; eski private
  key eski backup'lar saqlanguncha (30 kun) offline turadi.

## 8. Eskalatsiya

| Severity | Misol | Aloqa | Javob |
|---|---|---|---|
| **P0** | Production down (>5 daqiqa), ma'lumot sizishi, buzib kirish | Telegram @narzullayevme | < 15 daqiqa |
| **P1** | Auth ishlamaydi, to'lov o'tmaydi, imtihon boshlanmaydi | Telegram @narzullayevme | < 1 soat |
| **P2** | Sekin (p95 > 5 s), qisman nosozlik | Email (on-call) | Keyingi ish kuni |
| **P3** | Kosmetika, hujjat | GitHub Issue | Backlog |

Tashqi: PlayMobile `support@playmobile.uz`, Eskiz `info@eskiz.uz`,
https://status.anthropic.com, Postgres/Redis provayderi support tiketi.

## 9. Drill (har chorak)

- [ ] PostgreSQL: oxirgi backup → bo'sh bazaga restore, 1-bo'lim tekshiruvlari, vaqtni yozing (RTO)
- [ ] Backup Job xatosi alert'gacha yetishini tekshiring (`kubectl get jobs -l app.kubernetes.io/component=backup`)
- [ ] PgBouncer'ni o'chirib-yoqish (`rollout restart`): ilova o'zi tiklanadimi
- [ ] Redis restart: Celery vazifalari yo'qolmaydimi (acks_late), beat ishlaydimi
- [ ] Bitta secret rotation (staging)
- [ ] Bu hujjatdagi buyruqlar hali to'g'rimi

### Jurnal

| Sana | Nima | Natija |
|---|---|---|
| 2026-11-12 | Backup Job + to'liq restore (kind klasteri), `backup-restore.md` "Last rehearsal" | OK: checksum, `pg_restore --exit-on-error`, 17 RLS jadval, 124 migratsiya |

## Ma'lum bo'shliqlar

- **RPO 24 soat** — kunlik dump. Kamaytirish: provayder PITR yoqish yoki WAL arxivlash
  (`scripts/backup/` WAL-G skriptlari faqat o'zimiz boshqaradigan Postgres host uchun).
- **Media PVC backup qilinmaydi** — storage class snapshot'i yoki object storage
  (S3) ga o'tish kerak.

## Bog'liq hujjatlar

- `docs/operations/backup-restore.md` (repo ildizi) — backup sozlash va restore buyruqlari
- `docs/deployment/` — deploy, secret'lar
- `charts/yuzdanyuz/values.yaml` — `pgbouncer`, `backup`, `externalServices`
- `docs/pre_launch_checklist.md` — launch'dan oldingi DR tekshiruvi
