"""
ISSUE-104 — Custom Prometheus business metrics.

django-prometheus default'da HTTP/DB metric'lar. SRE business KPI'larni
ko'rishi uchun custom counter/histogram qo'shamiz.

Metrika nomlash: `yz_<domain>_<metric>_<unit>` (yuzdanyuz prefix).
Label'lar minimal kardinalitedа (org_id, status, provider — UUID o'rinli).

Foydalanish:
    from core.metrics import EXAMS_SUBMITTED, AI_TUTOR_LATENCY

    EXAMS_SUBMITTED.labels(org_id=str(org.id), is_public='true').inc()

    with AI_TUTOR_LATENCY.time():
        response = anthropic_client.messages.create(...)
"""

from prometheus_client import Counter, Gauge, Histogram

# ── Exam submissions ────────────────────────────────────────────────────────
EXAMS_SUBMITTED = Counter(
    'yz_exams_submitted_total',
    'Total exam attempts submitted (finalized score)',
    ['org_id', 'is_public'],
)

# ── Payments ────────────────────────────────────────────────────────────────
PAYMENTS_COMPLETED = Counter(
    'yz_payments_completed_total',
    'Payment webhook completions',
    ['provider', 'status'],  # provider=payme|click, status=succeeded|failed
)

# ── AI Tutor (Anthropic) ────────────────────────────────────────────────────
AI_TUTOR_CALLS = Counter(
    'yz_ai_tutor_calls_total',
    'Anthropic API calls for AI tutor + OpenEnded grading',
    ['task_type', 'status'],  # task_type=tutor|openended, status=ok|failed
)

AI_TUTOR_LATENCY = Histogram(
    'yz_ai_tutor_seconds',
    'Anthropic API call duration',
    ['task_type'],
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

# ── Leaderboard ─────────────────────────────────────────────────────────────
LEADERBOARD_UPDATES = Counter(
    'yz_leaderboard_updates_total',
    'Redis ZADD operations on leaderboard scopes',
    ['scope'],  # scope=global|region|tenant|mock
)

# ── SMS / Notifications ─────────────────────────────────────────────────────
SMS_SENT = Counter(
    'yz_sms_sent_total',
    'SMS notifications sent (cost tracking)',
    ['backend', 'status'],  # backend=console|playmobile|eskiz, status=ok|failed
)

# ISSUE-114: SMS provider balansi (UZS). Past bo'lsa OTP-based auth o'ladi.
SMS_BALANCE = Gauge(
    'yz_sms_balance_uzs',
    'Current SMS provider balance in UZS (low → OTP auth blocked)',
    ['backend'],  # backend=playmobile|eskiz
)
