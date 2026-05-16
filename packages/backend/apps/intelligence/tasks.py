"""
Task 6 — AI Diagnostika Celery tasks.

Tasklar:
  - generate_ai_tutor_feedback(feedback_id) — on-demand LLM call
  - evaluate_openended_submission(submission_id) — essay/audio AI scoring

LLM client: Anthropic SDK (apps/catalog'dan ishlatilgan pattern).
Test paytida `_generate_text` mock qilinadi.

Architecture (Bosqich 5+20):
  Backend deterministik xulosa hisoblaydi → LLM faqat motivatsion matn yozadi
  yoki rubric bo'yicha baholaydi. Hech qachon o'zidan qoida o'ylamaydi.
"""

import hashlib
import json
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import AIFeedback, OpenEndedSubmission

logger = logging.getLogger(__name__)


# ── LLM client wrapper ────────────────────────────────────────────────────────


def _generate_text(prompt: str, *, max_tokens: int = 1024, model: str = 'claude-sonnet-4-5') -> str:
    """
    Anthropic API'ga bitta so'rov. Test paytida bu funksiya monkey-patch qilinadi.

    Production'da: settings.ANTHROPIC_API_KEY .env'dan keladi.
    """
    import anthropic

    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY settings/env'da yo'q")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{'role': 'user', 'content': prompt}],
    )
    # Anthropic SDK 0.x: message.content[0].text
    return message.content[0].text if message.content else ''


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
            text = _generate_text(prompt)

        fb.content = text
        fb.status = AIFeedback.Status.READY
        fb.completed_at = timezone.now()
        fb.save(update_fields=['content', 'status', 'completed_at'])
        AI_TUTOR_CALLS.labels(task_type='tutor', status='ok').inc()
        return {'status': 'ready', 'feedback_id': str(fb.id)}
    except Exception as e:
        logger.exception('AI Tutor LLM failed for %s', feedback_id)
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
        text = _generate_text(prompt, max_tokens=512)

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
        logger.exception('Open-ended evaluation failed for %s', submission_id)
        sub.status = OpenEndedSubmission.Status.FAILED
        sub.ai_feedback = {'error': str(e)[:500]}
        sub.ai_completed_at = timezone.now()
        sub.save(update_fields=['status', 'ai_feedback', 'ai_completed_at'])
        return {'status': 'failed', 'error': str(e)[:200]}
