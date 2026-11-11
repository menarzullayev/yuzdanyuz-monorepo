"""
Task 6 — AI Diagnostika Celery tasks.

Tasklar:
  - generate_ai_tutor_feedback(feedback_id) — on-demand LLM call
  - evaluate_openended_submission(submission_id) — essay/audio AI scoring
  - check_ai_token_expiry() — daily reminder before the `claude setup-token` token expires

A rate limit or a timeout on the Claude subscription defers the task (retry
until the limit resets) instead of failing the record; an auth error fails it
at once and is tagged ai.error_kind=auth in Sentry.

LLM client: Anthropic SDK (apps/catalog'dan ishlatilgan pattern).
Test paytida `_generate_text` mock qilinadi.

Architecture (Bosqich 5+20):
  Backend deterministik xulosa hisoblaydi → LLM faqat motivatsion matn yozadi
  yoki rubric bo'yicha baholaydi. Hech qachon o'zidan qoida o'ylamaydi.
"""

import hashlib
import json
import logging
from datetime import date

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from core.claude_cli import ClaudeRateLimitError, ClaudeTimeoutError

from .models import AIFeedback, OpenEndedSubmission

logger = logging.getLogger(__name__)


# ── Retry policy for subscription limits ──────────────────────────────────────

AI_RETRY_MAX = 6  # deferred runs before the record is marked FAILED
_RATE_LIMIT_WAIT = 15 * 60  # when the CLI does not say when the limit resets
_MAX_WAIT = 6 * 60 * 60
_TIMEOUT_WAIT = 2 * 60


def ai_retry_countdown(exc: Exception) -> int | None:
    """Seconds to wait before retrying an AI call, or None when retrying cannot help."""
    if isinstance(exc, ClaudeRateLimitError):
        if exc.resets_at is None:
            return _RATE_LIMIT_WAIT
        wait = (exc.resets_at - timezone.now()).total_seconds() + 60
        return int(min(max(wait, 60), _MAX_WAIT))
    if isinstance(exc, ClaudeTimeoutError):
        return _TIMEOUT_WAIT
    return None


def _report_ai_failure(exc: Exception, what: str, obj_id: str) -> None:
    """Log the failure; LoggingIntegration turns it into a Sentry event tagged ai.error_kind."""
    kind = getattr(exc, 'kind', 'error')
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - sentry-sdk is in common.txt
        logger.exception('%s failed for %s (ai.error_kind=%s)', what, obj_id, kind)
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('ai.error_kind', kind)
        logger.exception('%s failed for %s (ai.error_kind=%s)', what, obj_id, kind)


# ── LLM client wrapper ────────────────────────────────────────────────────────


# Models that accept server-side refusal fallback (`fallbacks: "default"`).
# Exact IDs: a prefix match on 'claude-opus-5' would also catch other models.
_SERVER_FALLBACK_MODELS = frozenset(
    {
        'claude-sonnet-5-5',
        'claude-opus-5-5',
        'claude-opus-5',
        'claude-fable-5-1',
        'claude-fable-5-2',
    }
)


def _generate_text(
    prompt: str,
    *,
    max_tokens: int = 2048,
    model: str | None = None,
    system: str | None = None,
    json_schema: dict | None = None,
) -> str:
    """
    Claude'ga bitta so'rov. Test paytida bu funksiya monkey-patch qilinadi.

    settings.LLM_BACKEND tanlaydi: 'claude_cli' — mahalliy Claude subscription
    (`claude -p`, core/claude_cli.py); 'anthropic_api' — ANTHROPIC_API_KEY bilan API.
    Model ikkalasida ham settings.ANTHROPIC_MODEL.

    `json_schema` is enforced by the CLI backend (the reply is that JSON);
    the API backend relies on the prompt asking for JSON.

    ISSUE-X04: agar `system` berilsa, ephemeral prompt cache qo'llaniladi —
    bir xil system bloki qayta ishlatilganida token narxi sezilarli kamayadi.

    Claude Sonnet 5.5 thinks adaptively by default and thinking counts toward
    `max_tokens`, so the limit leaves room for it; `effort: low` keeps thinking
    short (and skips it on most of these short generation/scoring requests).
    """
    model = model or settings.ANTHROPIC_MODEL
    if settings.LLM_BACKEND == 'claude_cli':
        from core import claude_cli

        result = claude_cli.run(
            prompt,
            system=system or '',
            model=model,
            json_schema=json_schema,
            timeout=settings.CLAUDE_CLI_TIMEOUT,
        )
        if json_schema is not None:
            return json.dumps(result.structured, ensure_ascii=False)
        return result.text

    import anthropic

    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY settings/env'da yo'q")

    client = anthropic.Anthropic(api_key=api_key)
    kwargs: dict = {
        'model': model,
        'max_tokens': max_tokens,
        'output_config': {'effort': 'low'},
        'messages': [{'role': 'user', 'content': prompt}],
    }
    if system:
        # ISSUE-X04: cache_control=ephemeral on stable SYSTEM block
        kwargs['system'] = [
            {'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}},
        ]
    if model in _SERVER_FALLBACK_MODELS:
        # On a policy decline the API re-runs the request on a fallback model.
        message = client.beta.messages.create(
            betas=['server-side-fallback-2026-07-01'], fallbacks='default', **kwargs
        )
    else:
        message = client.messages.create(**kwargs)

    if message.stop_reason == 'refusal':
        category = getattr(message.stop_details, 'category', None)
        raise RuntimeError(f'Claude declined the request (refusal, category={category})')
    # A reply can start with a thinking block, so read text blocks by type.
    text = ''.join(block.text for block in message.content if block.type == 'text')
    if message.stop_reason == 'max_tokens':
        raise RuntimeError(f'Claude reply truncated at max_tokens={max_tokens}')
    return text


# ── Stable SYSTEM prompts (cacheable via Anthropic ephemeral cache) ───────────

TUTOR_SYSTEM_PROMPT = (
    "Sen O'zbekiston DTM imtihoniga tayyorlanayotgan o'quvchi uchun "
    "motivatsion ustozsan. Faqat O'zbek tilida, qisqa (4-5 jumla) javob ber. "
    "Berilgan raqamlarni o'zgartirma — faqat tahlil qil va tavsiya ber."
)

ESSAY_RUBRIC_SYSTEM_PROMPT = (
    "Sen O'zbek tili yoki ingliz tilida insho/javobni baholaydigan ekspertsan. "
    'Mezonlar: grammar, content, structure, relevance (har biri 0-100). '
    "Faqat valid JSON qaytar — hech qanday markdown yoki prefiks qo'shma."
)

_SCORE = {'type': 'integer', 'minimum': 0, 'maximum': 100}
ESSAY_RUBRIC_SCHEMA = {
    'type': 'object',
    'properties': {
        'grammar': _SCORE,
        'content': _SCORE,
        'structure': _SCORE,
        'relevance': _SCORE,
        'rationale': {'type': 'string'},
    },
    'required': ['grammar', 'content', 'structure', 'relevance', 'rationale'],
    'additionalProperties': False,
}


# ── 1. AI Tutor — on-demand motivational feedback ────────────────────────────


def _build_tutor_prompt(summary: dict) -> str:
    """Deterministik xulosadan O'zbek tilida motivatsion prompt yasash."""
    weak = summary.get('weak', [])
    strong = summary.get('strong', [])
    avg = summary.get('stats', {}).get('avg_mastery', 0)

    weak_text = (
        ', '.join(f'{s["skill_name"]} ({s["mastery"]:.0%})' for s in weak)
        if weak
        else 'aniqlanmagan'
    )
    strong_text = (
        ', '.join(f'{s["skill_name"]} ({s["mastery"]:.0%})' for s in strong)
        if strong
        else 'aniqlanmagan'
    )

    return f"""Sen O'zbekiston DTM imtihoniga tayyorlanayotgan o'quvchi uchun motivatsion ustozsan.

Quyidagi statistika berilgan (bu RAQAMLARNI O'ZGARTIRMA, faqat tahlil qil):

Zaif mavzular: {weak_text}
Kuchli mavzular: {strong_text}
O'rtacha mastery: {avg:.0%}

Topshiriq:
1. 2-3 jumla bilan o'quvchini ruhlantir.
2. Eng zaif 1-2 mavzuga konsentratsiya qilishni tavsiya qil.
3. Kuchli mavzulardan foydalanib o'z-o'ziga ishonchni oshirish maslahati ber.

Faqat O'zbek tilida yoz. Maksimum 4-5 jumla. Quyidagi format:

🎯 [motivatsion fikr]

📈 [zaif mavzularga maslahat]

💪 [kuchli tomonlardan foydalanish maslahati]"""


@shared_task(name='intelligence.generate_ai_tutor_feedback', bind=True)
def generate_ai_tutor_feedback(self, feedback_id: str) -> dict:
    """
    AIFeedback record uchun LLM javobini generate qilish.
    feedback_id frontend'dan keladi (REST POST tomonidan yaratilgan).
    """
    try:
        fb = AIFeedback.objects.get(pk=feedback_id)
    except AIFeedback.DoesNotExist:
        logger.warning('generate_ai_tutor_feedback: %s topilmadi', feedback_id)
        return {'status': 'not_found'}

    if fb.status == AIFeedback.Status.READY:
        return {'status': 'already_ready'}

    # ISSUE-104: business metric — Anthropic call counter + latency
    from core.metrics import AI_TUTOR_CALLS, AI_TUTOR_LATENCY

    try:
        prompt = _build_tutor_prompt(fb.summary)
        with AI_TUTOR_LATENCY.labels(task_type='tutor').time():
            text = _generate_text(prompt, system=TUTOR_SYSTEM_PROMPT)

        fb.content = text
        fb.status = AIFeedback.Status.READY
        fb.completed_at = timezone.now()
        fb.save(update_fields=['content', 'status', 'completed_at'])
        AI_TUTOR_CALLS.labels(task_type='tutor', status='ok').inc()
        return {'status': 'ready', 'feedback_id': str(fb.id)}
    except Exception as e:
        countdown = ai_retry_countdown(e)
        if countdown is not None and self.request.retries < AI_RETRY_MAX:
            logger.warning('AI Tutor for %s deferred %ss: %s', feedback_id, countdown, e)
            AI_TUTOR_CALLS.labels(task_type='tutor', status='deferred').inc()
            raise self.retry(exc=e, countdown=countdown, max_retries=AI_RETRY_MAX) from e
        _report_ai_failure(e, 'AI Tutor', feedback_id)
        fb.status = AIFeedback.Status.FAILED
        fb.error = str(e)[:1000]
        fb.completed_at = timezone.now()
        fb.save(update_fields=['status', 'error', 'completed_at'])
        AI_TUTOR_CALLS.labels(task_type='tutor', status='failed').inc()
        return {'status': 'failed', 'error': str(e)[:200]}


def compute_summary_hash(summary: dict) -> str:
    """Cache key — same summary → cached LLM response."""
    payload = json.dumps(summary, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


# ── 2. Open-Ended Evaluation ─────────────────────────────────────────────────


def _build_essay_rubric_prompt(question_text: str, answer_text: str) -> str:
    """Essay/audio answer'ni rubric bo'yicha baholash uchun prompt."""
    return f"""Sen O'zbek tili yoki ingliz tilida insho/javobni baholaydigan ekspertsan.

SAVOL:
{question_text}

O'QUVCHI JAVOBI:
{answer_text}

Quyidagi 4 mezon bo'yicha 0-100 oralig'ida ball qo'y va JSON qaytar:
- grammar: grammatik to'g'rilik
- content: mavzuni qamrab olish
- structure: tuzilma va izchillik
- relevance: savolga aniq javob

JSON FORMATI (faqat shu, boshqa matn yozma):
{{"grammar": <int>, "content": <int>, "structure": <int>, "relevance": <int>, "rationale": "<2-3 jumla O'zbek tilida tushuntirish>"}}"""


@shared_task(name='intelligence.evaluate_openended_submission', bind=True)
def evaluate_openended_submission(self, submission_id: str) -> dict:
    """
    OpenEndedSubmission'ni AI bilan baholash. Lifecycle:
      PENDING → AI_REVIEWED (default) yoki FAILED
    Human QA navbatdagi qadamda (admin tomonidan).
    """
    try:
        sub = OpenEndedSubmission.objects.select_related('question_version').get(pk=submission_id)
    except OpenEndedSubmission.DoesNotExist:
        logger.warning('evaluate_openended_submission: %s topilmadi', submission_id)
        return {'status': 'not_found'}

    if sub.status != OpenEndedSubmission.Status.PENDING:
        return {'status': 'skipped', 'reason': sub.status}

    try:
        question_text = sub.question_version.content.get('text', '') or ''
        answer_text = sub.content or ''

        if sub.submission_type == OpenEndedSubmission.Type.AUDIO and not answer_text:
            # Production: Whisper API transcribe. Hozircha placeholder.
            raise RuntimeError('Audio transcription hali implement qilinmagan (Whisper SDK kerak)')

        prompt = _build_essay_rubric_prompt(question_text, answer_text)
        text = _generate_text(
            prompt, system=ESSAY_RUBRIC_SYSTEM_PROMPT, json_schema=ESSAY_RUBRIC_SCHEMA
        )

        # JSON parse
        try:
            scores = json.loads(text.strip().strip('`').replace('json\n', '', 1))
        except json.JSONDecodeError as e:
            raise RuntimeError(f'LLM JSON parse failed: {e}') from None

        # Aggregate score (oddiy avg — kelajakda weighted bo'lishi mumkin)
        breakdown = {
            k: int(scores.get(k, 0)) for k in ['grammar', 'content', 'structure', 'relevance']
        }
        avg_score = sum(breakdown.values()) / 4

        sub.ai_score = round(avg_score, 2)
        sub.ai_feedback = {**breakdown, 'rationale': scores.get('rationale', '')}
        sub.status = OpenEndedSubmission.Status.AI_REVIEWED
        sub.ai_completed_at = timezone.now()
        sub.save(update_fields=['ai_score', 'ai_feedback', 'status', 'ai_completed_at'])
        return {
            'status': 'ai_reviewed',
            'submission_id': str(sub.id),
            'score': float(sub.ai_score),
        }
    except Exception as e:
        countdown = ai_retry_countdown(e)
        if countdown is not None and self.request.retries < AI_RETRY_MAX:
            logger.warning('Open-ended %s deferred %ss: %s', submission_id, countdown, e)
            raise self.retry(exc=e, countdown=countdown, max_retries=AI_RETRY_MAX) from e
        _report_ai_failure(e, 'Open-ended evaluation', submission_id)
        sub.status = OpenEndedSubmission.Status.FAILED
        sub.ai_feedback = {'error': str(e)[:500]}
        sub.ai_completed_at = timezone.now()
        sub.save(update_fields=['status', 'ai_feedback', 'ai_completed_at'])
        return {'status': 'failed', 'error': str(e)[:200]}


# ── 3. Claude subscription token expiry ──────────────────────────────────────

TOKEN_WARN_DAYS = 30
TOKEN_URGENT_DAYS = 14


def ai_token_days_left(today: date | None = None) -> int | None:
    """Days until CLAUDE_CODE_OAUTH_TOKEN_EXPIRES (YYYY-MM-DD); None when unset.

    Raises ValueError when the setting is not an ISO date.
    """
    raw = (getattr(settings, 'CLAUDE_CODE_OAUTH_TOKEN_EXPIRES', '') or '').strip()
    if not raw:
        return None
    return (date.fromisoformat(raw) - (today or timezone.localdate())).days


@shared_task(name='intelligence.check_ai_token_expiry')
def check_ai_token_expiry() -> dict:
    """Daily (core/celery.py beat_schedule). ERROR logs reach Sentry via LoggingIntegration."""
    if settings.LLM_BACKEND != 'claude_cli':
        return {'status': 'not_used'}
    try:
        days = ai_token_days_left()
    except ValueError:
        logger.error(
            'CLAUDE_CODE_OAUTH_TOKEN_EXPIRES is not a YYYY-MM-DD date: %r',
            settings.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES,
        )
        return {'status': 'invalid'}
    if days is None:
        logger.warning('CLAUDE_CODE_OAUTH_TOKEN_EXPIRES is not set; token expiry is not tracked')
        return {'status': 'unknown'}

    renew = 'run `claude setup-token` and update CLAUDE_CODE_OAUTH_TOKEN and its _EXPIRES date'
    if days < 0:
        logger.error(
            'Claude subscription token expired %s day(s) ago; AI is down: %s', -days, renew
        )
        status = 'expired'
    elif days <= TOKEN_URGENT_DAYS:
        logger.error('Claude subscription token expires in %s day(s): %s', days, renew)
        status = 'urgent'
    elif days <= TOKEN_WARN_DAYS:
        logger.warning('Claude subscription token expires in %s day(s): %s', days, renew)
        status = 'soon'
    else:
        status = 'ok'
    return {'status': status, 'days_left': days}
