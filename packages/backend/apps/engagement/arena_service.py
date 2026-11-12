"""Arena business rules: visibility, review workflow, timed play, scoring.

Scoring per question: a correct answer inside the time limit earns 500 points plus up to
500 more for speed (linear in the unused share of the limit); wrong or late = 0.
The timer is server-side: `QuizPlay.question_started_at` is the only clock.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion

from .arena_models import (
    MAX_QUESTIONS,
    MIN_QUESTIONS_TO_SUBMIT,
    Quiz,
    QuizPlay,
    QuizPlayAnswer,
    QuizQuestion,
)
from .permissions import is_org_admin

BASE_POINTS = 500
SPEED_BONUS_MAX = 500
LATE_GRACE_MS = 1500  # network latency allowance on top of the per-question limit
PLAYABLE_TYPES = (Question.Type.SINGLE_CHOICE, Question.Type.MULTIPLE_CHOICE)


class ArenaError(Exception):
    """Business-rule violation; `status` maps to the HTTP code."""

    status = 400

    def __init__(self, message, status=None):
        super().__init__(message)
        if status is not None:
            self.status = status


def _now():
    return timezone.now()


# -- visibility --------------------------------------------------------------


def visible_quizzes(user, org):
    """Quizzes the user may see. Students/teachers never see other people's unapproved ones."""
    qs = Quiz.objects.all()  # TenantManager already scopes to the request org
    if is_org_admin(user, org):
        return qs
    return qs.filter(Q(status=Quiz.Status.APPROVED) | Q(created_by=user))


def can_edit(user, org, quiz) -> bool:
    return quiz.status == Quiz.Status.DRAFT and (
        quiz.created_by_id == user.id or is_org_admin(user, org)
    )


# -- content -----------------------------------------------------------------


def set_questions(quiz, org, version_ids):
    ids = list(dict.fromkeys(version_ids))  # keep order, drop duplicates
    if len(ids) > MAX_QUESTIONS:
        raise ArenaError(f'A quiz can have at most {MAX_QUESTIONS} questions.')
    found = {
        qv.pk: qv
        for qv in QuestionVersion.objects.filter(
            pk__in=ids,
            question__organization=org,
            question__type__in=PLAYABLE_TYPES,
            question__is_active=True,
            is_quarantined=False,
        )
    }
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise ArenaError(
            f'Unusable question versions (not in this organization, wrong type, or inactive): {missing}'
        )
    with transaction.atomic():
        QuizQuestion.objects.filter(quiz=quiz).delete()
        QuizQuestion.objects.bulk_create(
            [
                QuizQuestion(organization=org, quiz=quiz, question_version=found[pk], order=i + 1)
                for i, pk in enumerate(ids)
            ]
        )


# -- review workflow ---------------------------------------------------------


def submit_for_review(quiz, user, org):
    if not (quiz.created_by_id == user.id or is_org_admin(user, org)):
        raise ArenaError('Only the author or an admin can submit this quiz.', 403)
    if quiz.items.count() < MIN_QUESTIONS_TO_SUBMIT:
        raise ArenaError(f'A quiz needs at least {MIN_QUESTIONS_TO_SUBMIT} questions.')
    author_is_admin = quiz.created_by is not None and is_org_admin(quiz.created_by, org)
    try:
        if author_is_admin:
            quiz.transition(Quiz.Status.APPROVED)
            quiz.reviewed_by = quiz.created_by
            quiz.reviewed_at = _now()
            quiz.review_note = 'auto-approved: author is an admin'
        else:
            quiz.transition(Quiz.Status.PENDING_REVIEW)
    except ValueError as e:
        raise ArenaError(str(e), 409) from e
    quiz.save()
    return quiz


def approve(quiz, user, org):
    if not is_org_admin(user, org):
        raise ArenaError('Only an admin can approve quizzes.', 403)
    if quiz.created_by_id == user.id:
        raise ArenaError('You cannot approve your own quiz.', 403)
    if quiz.status != Quiz.Status.PENDING_REVIEW:
        raise ArenaError('Only quizzes pending review can be approved.', 409)
    quiz.transition(Quiz.Status.APPROVED)
    quiz.reviewed_by = user
    quiz.reviewed_at = _now()
    quiz.review_note = ''
    quiz.save()
    return quiz


def reject(quiz, user, org, note):
    if not is_org_admin(user, org):
        raise ArenaError('Only an admin can reject quizzes.', 403)
    if quiz.created_by_id == user.id:
        raise ArenaError('You cannot review your own quiz.', 403)
    if not note.strip():
        raise ArenaError('A rejection note is required.')
    if quiz.status != Quiz.Status.PENDING_REVIEW:
        raise ArenaError('Only quizzes pending review can be rejected.', 409)
    quiz.transition(Quiz.Status.DRAFT)
    quiz.reviewed_by = user
    quiz.reviewed_at = _now()
    quiz.review_note = note.strip()
    quiz.save()
    return quiz


def archive(quiz, user, org):
    if not (quiz.created_by_id == user.id or is_org_admin(user, org)):
        raise ArenaError('Only the author or an admin can archive this quiz.', 403)
    try:
        quiz.transition(Quiz.Status.ARCHIVED)
    except ValueError as e:
        raise ArenaError(str(e), 409) from e
    quiz.save()
    return quiz


# -- play --------------------------------------------------------------------


def _items(quiz):
    return list(quiz.items.select_related('question_version__question').order_by('order'))


def question_payload(item, *, include_answers=False):
    qv = item.question_version
    options = qv.options if isinstance(qv.options, list) else []
    if not include_answers:
        options = [{k: v for k, v in o.items() if k != 'is_correct'} for o in options]
    return {
        'id': str(item.id),
        'order': item.order,
        'type': qv.question.type,
        'content': qv.content,
        'options': options,
    }


def _is_correct(qv, selected) -> bool:
    options = qv.options if isinstance(qv.options, list) else []
    correct = {o['id'] for o in options if o.get('is_correct')}
    if not correct:
        return False
    if qv.question.type == Question.Type.SINGLE_CHOICE:
        if isinstance(selected, dict):
            selected = selected.get('id')
        return selected in correct and len(correct) == 1
    if isinstance(selected, list):
        try:
            return set(selected) == correct
        except TypeError:
            return False
    return False


def play_state(play):
    quiz = play.quiz
    items = _items(quiz)
    state = {
        'quiz_id': str(quiz.id),
        'total': len(items),
        'index': play.current_index,
        'points': play.points,
        'correct_count': play.correct_count,
        'finished': play.finished_at is not None,
        'seconds_per_question': quiz.seconds_per_question,
    }
    if play.finished_at is None and play.current_index < len(items):
        state['question'] = question_payload(items[play.current_index])
        state['deadline'] = play.question_started_at + timedelta(seconds=quiz.seconds_per_question)
    return state


def start_play(quiz, user, org):
    if quiz.status != Quiz.Status.APPROVED:
        raise ArenaError('This quiz is not available.', 404)
    if not quiz.items.exists():
        raise ArenaError('This quiz has no questions.', 409)
    play, created = QuizPlay.objects.select_related('quiz').get_or_create(
        quiz=quiz,
        user=user,
        defaults={'organization': org, 'question_started_at': _now()},
    )
    if not created and play.finished_at is not None:
        raise ArenaError('You have already played this quiz.', 409)
    return play


def answer(play, item_id, selected):
    """Score the answer for the play's current question; the server clock decides lateness."""
    with transaction.atomic():
        play = QuizPlay.objects.select_for_update().select_related('quiz').get(pk=play.pk)
        if play.finished_at is not None:
            raise ArenaError('This play is already finished.', 409)
        items = _items(play.quiz)
        item = items[play.current_index]
        if str(item.id) != str(item_id):
            raise ArenaError('That is not the current question.', 409)

        now = _now()
        limit_ms = play.quiz.seconds_per_question * 1000
        elapsed_ms = int((now - play.question_started_at).total_seconds() * 1000)
        timed_out = selected is None or elapsed_ms > limit_ms + LATE_GRACE_MS
        is_correct = (not timed_out) and _is_correct(item.question_version, selected)
        points = 0
        if is_correct:
            unused = max(0, limit_ms - min(elapsed_ms, limit_ms)) / limit_ms
            points = BASE_POINTS + round(SPEED_BONUS_MAX * unused)

        QuizPlayAnswer.objects.create(
            organization=play.organization,
            play=play,
            quiz_question=item,
            selected=selected,
            is_correct=is_correct,
            timed_out=timed_out,
            elapsed_ms=min(elapsed_ms, limit_ms),
            points=points,
        )
        play.points += points
        play.correct_count += int(is_correct)
        play.total_time_ms += min(elapsed_ms, limit_ms)
        play.current_index += 1
        if play.current_index >= len(items):
            play.finished_at = now
        else:
            play.question_started_at = now
        play.save()

    correct_ids = [o['id'] for o in (item.question_version.options or []) if o.get('is_correct')]
    return play, {
        'is_correct': is_correct,
        'timed_out': timed_out,
        'points_awarded': points,
        'correct_option_ids': correct_ids,
    }


def leaderboard(quiz, user, limit=50):
    qs = QuizPlay.objects.filter(quiz=quiz, finished_at__isnull=False).select_related('user')
    ordered = qs.order_by('-points', 'total_time_ms', 'finished_at')
    rows = [
        {
            'rank': i + 1,
            'user_id': str(p.user_id),
            'username': p.user.username,
            'points': p.points,
            'correct_count': p.correct_count,
            'total_time_ms': p.total_time_ms,
        }
        for i, p in enumerate(ordered[:limit])
    ]
    me = None
    mine = qs.filter(user=user).first()
    if mine:
        ahead = qs.filter(
            Q(points__gt=mine.points)
            | Q(points=mine.points, total_time_ms__lt=mine.total_time_ms)
            | Q(
                points=mine.points,
                total_time_ms=mine.total_time_ms,
                finished_at__lt=mine.finished_at,
            )
        ).count()
        me = {'rank': ahead + 1, 'points': mine.points, 'total_time_ms': mine.total_time_ms}
    return {'total': qs.count(), 'entries': rows, 'me': me}
