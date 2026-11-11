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

from apps.catalog.services.ai_providers.base import TransientProviderError
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
    if isinstance(exc, ClaudeTimeoutError | TransientProviderError):
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


def _generate_text(
    prompt: str,
    *,
    max_tokens: int = 2048,
    model: str | None = None,
    system: str | None = None,
    json_schema: dict | None = None,
    profile: str | None = None,
) -> str:
    """
    One LLM request. Tests monkeypatch this function.

    profile=None -> the settings backend (LLM_BACKEND: 'claude_cli' = local Claude
    subscription via core/claude_cli.py, 'anthropic_api' = ANTHROPIC_API_KEY) with
    settings.ANTHROPIC_MODEL (or `model`). A profile ('default'|'writing'|'speaking')
    selects the admin-configured AIProviderConfig rows instead (apps/intelligence/llm.py),
    falling back to the default profile and then to the settings backend.

    `json_schema` is enforced by the CLI backend and requested via JSON mode by the HTTP
    providers; the Anthropic API backend relies on the prompt asking for JSON.
    """
    from . import llm

    if profile is not None:
        return llm.generate(
            prompt,
            profile=profile,
            system=system or '',
            json_schema=json_schema,
            max_tokens=max_tokens,
        )
    return llm.settings_provider(model).complete(
        prompt, system=system or '', json_schema=json_schema, max_tokens=max_tokens
    )


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
            text = _generate_text(prompt, system=TUTOR_SYSTEM_PROMPT, profile='default')

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

WRITING_CRITERIA = ('grammar', 'content', 'structure', 'relevance')
# Speaking is scored from the transcript only, so pronunciation cannot be assessed.
SPEAKING_CRITERIA = ('fluency', 'lexical_resource', 'grammar')
SPEAKING_NOT_ASSESSED = ('pronunciation',)

SPEAKING_RUBRIC_SYSTEM_PROMPT = (
    "Sen o'quvchining og'zaki javobini TRANSKRIPT asosida baholaydigan ekspertsan. "
    'Mezonlar (har biri 0-100): fluency (ravonlik va izchillik), '
    "lexical_resource (lug'at boyligi va aniqligi), grammar (grammatik to'g'rilik). "
    "Talaffuz (pronunciation) BAHOLANMAYDI: sen audio'ni eshitmaysan, faqat matnni ko'rasan. "
    "Faqat valid JSON qaytar — hech qanday markdown yoki prefiks qo'shma."
)

SPEAKING_RUBRIC_SCHEMA = {
    'type': 'object',
    'properties': {**dict.fromkeys(SPEAKING_CRITERIA, _SCORE), 'rationale': {'type': 'string'}},
    'required': [*SPEAKING_CRITERIA, 'rationale'],
    'additionalProperties': False,
}


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


def _build_speaking_rubric_prompt(question_text: str, transcript: str) -> str:
    return f"""SAVOL:
{question_text}

O'QUVCHI JAVOBI (audio'dan olingan transkript):
{transcript}

Quyidagi 3 mezon bo'yicha 0-100 oralig'ida ball qo'y. Talaffuzni baholama (audio yo'q).
- fluency: ravonlik, izchillik, to'xtamlar va takrorlar (transkriptdan ko'rinishicha)
- lexical_resource: lug'at boyligi va so'z tanlash aniqligi
- grammar: grammatik to'g'rilik va murakkablik

JSON FORMATI (faqat shu, boshqa matn yozma):
{{"fluency": <int>, "lexical_resource": <int>, "grammar": <int>, "rationale": "<2-3 jumla O'zbek tilida tushuntirish>"}}"""


def _parse_scores(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.strip('`')
        if cleaned.startswith('json'):
            cleaned = cleaned[4:]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'LLM JSON parse failed: {e}') from None
    if not isinstance(data, dict):
        raise RuntimeError('LLM JSON parse failed: not an object')
    return data


def _extract_breakdown(scores: dict, criteria: tuple[str, ...]) -> dict[str, int]:
    """Every criterion must be present and numeric; values are clamped to 0..100."""
    breakdown = {}
    for key in criteria:
        value = scores.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise RuntimeError(f'LLM omitted or malformed criterion {key!r}')
        breakdown[key] = max(0, min(100, round(value)))
    return breakdown


def _read_audio(sub: OpenEndedSubmission) -> tuple[bytes, str]:
    if not sub.audio_file:
        raise RuntimeError("Audio fayl ham, transkript ham yo'q")
    with sub.audio_file.open('rb') as f:
        return f.read(), sub.audio_file.name.rsplit('/', 1)[-1]


@shared_task(name='intelligence.evaluate_openended_submission', bind=True)
def evaluate_openended_submission(self, submission_id: str) -> dict:
    """
    OpenEndedSubmission'ni AI bilan baholash. Lifecycle:
      PENDING → AI_REVIEWED (default), AWAITING_HUMAN (speaking, no STT provider) yoki FAILED
    The AI score is an estimate; the final score is set by a teacher (grading queue).
    Essay -> 'writing' profile; audio -> transcript via the speaking profile's STT, then
    'speaking' profile scores fluency, lexical resource and grammar (not pronunciation).
    """
    from core.metrics import AI_TUTOR_CALLS

    try:
        sub = OpenEndedSubmission.objects.select_related('question_version').get(pk=submission_id)
    except OpenEndedSubmission.DoesNotExist:
        logger.warning('evaluate_openended_submission: %s topilmadi', submission_id)
        return {'status': 'not_found'}

    if sub.status != OpenEndedSubmission.Status.PENDING:
        return {'status': 'skipped', 'reason': sub.status}

    is_audio = sub.submission_type == OpenEndedSubmission.Type.AUDIO
    try:
        question_text = sub.question_version.content.get('text', '') or ''
        answer_text = sub.content or ''

        if is_audio and not answer_text:
            from . import stt

            stt_config = stt.resolve_stt('speaking')
            if stt_config is None:
                sub.status = OpenEndedSubmission.Status.AWAITING_HUMAN
                sub.ai_feedback = {
                    'skipped': 'no_stt_provider',
                    'message': 'No speech-to-text provider is configured; awaiting a human grader.',
                }
                sub.save(update_fields=['status', 'ai_feedback'])
                AI_TUTOR_CALLS.labels(task_type='openended', status='skipped').inc()
                return {'status': 'awaiting_human', 'reason': 'no_stt_provider'}
            audio, filename = _read_audio(sub)
            answer_text = stt.transcribe(stt_config, audio, filename)
            sub.content = answer_text
            sub.save(update_fields=['content'])

        if is_audio:
            criteria, profile = SPEAKING_CRITERIA, 'speaking'
            prompt = _build_speaking_rubric_prompt(question_text, answer_text)
            system, schema = SPEAKING_RUBRIC_SYSTEM_PROMPT, SPEAKING_RUBRIC_SCHEMA
        else:
            criteria, profile = WRITING_CRITERIA, 'writing'
            prompt = _build_essay_rubric_prompt(question_text, answer_text)
            system, schema = ESSAY_RUBRIC_SYSTEM_PROMPT, ESSAY_RUBRIC_SCHEMA

        text = _generate_text(prompt, system=system, json_schema=schema, profile=profile)
        scores = _parse_scores(text)
        breakdown = _extract_breakdown(scores, criteria)
        avg_score = sum(breakdown.values()) / len(criteria)

        sub.ai_score = round(avg_score, 2)
        sub.ai_feedback = {**breakdown, 'rationale': scores.get('rationale', '')}
        if is_audio:
            sub.ai_feedback['based_on'] = 'transcript'
            sub.ai_feedback['not_assessed'] = list(SPEAKING_NOT_ASSESSED)
        sub.status = OpenEndedSubmission.Status.AI_REVIEWED
        sub.ai_completed_at = timezone.now()
        sub.save(update_fields=['ai_score', 'ai_feedback', 'status', 'ai_completed_at'])
        AI_TUTOR_CALLS.labels(task_type='openended', status='ok').inc()
        return {
            'status': 'ai_reviewed',
            'submission_id': str(sub.id),
            'score': float(sub.ai_score),
        }
    except Exception as e:
        countdown = ai_retry_countdown(e)
        if countdown is not None and self.request.retries < AI_RETRY_MAX:
            logger.warning('Open-ended %s deferred %ss: %s', submission_id, countdown, e)
            AI_TUTOR_CALLS.labels(task_type='openended', status='deferred').inc()
            raise self.retry(exc=e, countdown=countdown, max_retries=AI_RETRY_MAX) from e
        _report_ai_failure(e, 'Open-ended evaluation', submission_id)
        sub.status = OpenEndedSubmission.Status.FAILED
        sub.ai_feedback = {'error': str(e)[:500]}
        sub.ai_completed_at = timezone.now()
        sub.save(update_fields=['status', 'ai_feedback', 'ai_completed_at'])
        AI_TUTOR_CALLS.labels(task_type='openended', status='failed').inc()
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
