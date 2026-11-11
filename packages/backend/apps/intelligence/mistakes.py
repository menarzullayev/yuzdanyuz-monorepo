"""
Spaced-repetition mistake notebook (xatolar daftari) service layer.

Schedule: a card waits for its review after 1, 3, 7 and 21 days. A correct
review advances the stage; correct at the last stage masters the card. A wrong
review, or a new wrong exam answer to the same question, resets it to stage 1.

All day arithmetic is done on the Asia/Tashkent calendar: `due_at` is midnight
(start of day) Tashkent time, so "due tomorrow" means from 00:00 local tomorrow.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion

from .models import MistakeCard

TASHKENT = ZoneInfo('Asia/Tashkent')

# stage -> days to wait (counted from the day the stage was entered)
STAGE_INTERVAL_DAYS = {1: 1, 2: 3, 3: 7, 4: 21}
FINAL_STAGE = max(STAGE_INTERVAL_DAYS)

# Only types the exams app can grade automatically (_evaluate_answer).
REVIEWABLE_TYPES = (Question.Type.SINGLE_CHOICE, Question.Type.MULTIPLE_CHOICE)


class ReviewError(ValueError):
    """Base class for refused reviews."""


class NotDueError(ReviewError):
    pass


class AlreadyMasteredError(ReviewError):
    pass


def start_of_day_after(days: int, now: datetime | None = None) -> datetime:
    """00:00 Asia/Tashkent, `days` calendar days after the local date of `now`."""
    now = now or timezone.now()
    local_date = now.astimezone(TASHKENT).date()
    return datetime.combine(local_date + timedelta(days=days), time.min, tzinfo=TASHKENT)


def record_mistake(answer, now: datetime | None = None) -> MistakeCard | None:
    """
    Put the question of a wrongly answered UserAnswer into the user's notebook.

    Creates the card at stage 1 (due tomorrow) or resets an existing one
    (also a mastered one). Re-saving the same answer is a no-op, so progress
    is not wiped by an update of the answer that created the card.
    """
    if answer.is_correct is not False or answer.auto_correct:
        return None

    question = answer.question_version.question
    if question.type not in REVIEWABLE_TYPES:
        return None

    owner = answer.attempt if answer.attempt_id else answer.session
    due_at = start_of_day_after(STAGE_INTERVAL_DAYS[1], now)

    with transaction.atomic():
        # global_objects: the lookup must not depend on the ambient tenant context
        card, created = MistakeCard.global_objects.select_for_update().get_or_create(
            user_id=owner.user_id,
            question=question,
            defaults={
                'organization_id': answer.organization_id,
                'stage': 1,
                'due_at': due_at,
                'source_answer': answer,
                'source_attempt_id': answer.attempt_id,
                'source_session_id': answer.session_id,
            },
        )
        if created or card.source_answer_id == answer.id:
            return card

        card.stage = 1
        card.due_at = due_at
        card.mastered_at = None
        card.lapses += 1
        card.source_answer = answer
        card.source_attempt_id = answer.attempt_id
        card.source_session_id = answer.session_id
        card.save()
        return card


def current_version(question: Question) -> QuestionVersion | None:
    """The version shown in review: `current_version`, else the newest one."""
    versions = list(question.versions.all())  # prefetch-friendly
    for v in versions:
        if v.version_number == question.current_version:
            return v
    return max(versions, key=lambda v: v.version_number, default=None)


def review_card(card_id, user, selected, now: datetime | None = None) -> dict:
    """
    Grade a review answer for one of `user`'s cards and move the schedule.

    Raises MistakeCard.DoesNotExist for a missing card or one that belongs to
    another user, NotDueError / AlreadyMasteredError for refused reviews.
    """
    # Same correctness logic as the exams app (lazy: exams.views is heavy).
    from apps.exams.views import _evaluate_answer

    now = now or timezone.now()
    with transaction.atomic():
        card = (
            MistakeCard.objects.select_for_update()
            .select_related('question')
            .get(pk=card_id, user=user)
        )
        if card.mastered_at is not None:
            raise AlreadyMasteredError('Card is already mastered.')
        if card.due_at > now:
            raise NotDueError('Card is not due yet.')

        version = current_version(card.question)
        if version is None:
            raise ReviewError('Question has no versions.')
        correct = _evaluate_answer(version, selected)

        card.last_reviewed_at = now
        if correct and card.stage >= FINAL_STAGE:
            card.mastered_at = now
        elif correct:
            card.stage += 1
            card.due_at = start_of_day_after(STAGE_INTERVAL_DAYS[card.stage], now)
        else:
            card.stage = 1
            card.due_at = start_of_day_after(STAGE_INTERVAL_DAYS[1], now)
            card.lapses += 1
        card.save()

    correct_ids = sorted(o['id'] for o in (version.options or []) if o.get('is_correct'))
    return {
        'card': card,
        'correct': correct,
        'correct_option_ids': correct_ids,
        'explanation': version.explanation,
    }
