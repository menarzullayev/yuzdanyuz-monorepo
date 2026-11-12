"""Typing practice scoring. Client-sent wpm/accuracy are never read.

net WPM = (correct_chars / 5) / minutes; accuracy = correct_chars / typed_chars * 100.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, Max

from .typing_models import TypingRun

TYPING_MIN_DURATION_MS = 5_000
TYPING_MAX_DURATION_MS = 900_000
TYPING_MAX_WPM = Decimal('250')
# Only clean runs count for personal best / leaderboard, so mashing keys can't top them.
TYPING_MIN_ACCURACY_FOR_BEST = Decimal('90')
LEADERBOARD_SIZE = 20

_Q2 = Decimal('0.01')


class TypingRejected(ValueError):
    pass


def score_run(*, text_length: int, duration_ms: int, typed_chars: int, errors: int):
    """Validate the keystroke summary and return (correct_chars, wpm, accuracy)."""
    if duration_ms < TYPING_MIN_DURATION_MS:
        raise TypingRejected('Run is too short (minimum 5 seconds).')
    if duration_ms > TYPING_MAX_DURATION_MS:
        raise TypingRejected('Run is too long (maximum 15 minutes).')
    if typed_chars < 1:
        raise TypingRejected('No characters typed.')
    if errors > typed_chars:
        raise TypingRejected('errors cannot exceed typed_chars.')
    correct = typed_chars - errors
    if correct > text_length:
        raise TypingRejected('More correct characters than the text contains.')
    minutes = Decimal(duration_ms) / Decimal(60_000)
    wpm = (Decimal(correct) / 5 / minutes).quantize(_Q2, ROUND_HALF_UP)
    gross = Decimal(typed_chars) / 5 / minutes
    if wpm > TYPING_MAX_WPM or gross > TYPING_MAX_WPM:
        raise TypingRejected('Implausible typing speed.')
    accuracy = (Decimal(correct) * 100 / Decimal(typed_chars)).quantize(_Q2, ROUND_HALF_UP)
    return correct, wpm, accuracy


def clean_runs(user=None, language=None):
    qs = TypingRun.objects.filter(accuracy__gte=TYPING_MIN_ACCURACY_FOR_BEST)
    if user is not None:
        qs = qs.filter(user=user)
    if language:
        qs = qs.filter(language=language)
    return qs


def personal_best(user, language):
    return clean_runs(user, language).order_by('-wpm', 'created_at').first()


def leaderboard(language: str, user=None):
    board = (
        clean_runs(language=language)
        .values('user_id', 'user__username')
        .annotate(best_wpm=Max('wpm'), runs=Count('id'))
        .order_by('-best_wpm', 'user__username')
    )
    entries = [
        {
            'rank': i + 1,
            'user_id': str(r['user_id']),
            'username': r['user__username'],
            'best_wpm': r['best_wpm'],
        }
        for i, r in enumerate(board[:LEADERBOARD_SIZE])
    ]
    me = None
    if user is not None:
        best = personal_best(user, language)
        if best:
            ahead = (
                clean_runs(language=language)
                .values('user_id')
                .annotate(b=Max('wpm'))
                .filter(b__gt=best.wpm)
                .count()
            )
            me = {'rank': ahead + 1, 'best_wpm': best.wpm}
    return {'language': language, 'entries': entries, 'me': me}
