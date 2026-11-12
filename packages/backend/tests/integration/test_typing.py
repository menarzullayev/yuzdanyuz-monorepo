"""Typing practice: server-side scoring, plausibility checks, best, history, leaderboard."""

from decimal import Decimal

import pytest

from apps.engagement.typing_models import TypingRun, TypingText
from apps.engagement.typing_service import TypingRejected, score_run

from .engagement_helpers import client, member

pytestmark = pytest.mark.integration

BASE = '/api/v1/engagement/typing/'
BODY = 'x' * 400


@pytest.fixture
def text(db):
    return TypingText.objects.create(language='en', title='T', body=BODY)


@pytest.fixture
def uz(db):
    return TypingText.objects.create(language='uz-Latn', title='U', body=BODY)


@pytest.fixture
def u(db, org):
    return member(org, 'typist', 'student')


def _run(c, text, **kw):
    payload = {'text_id': str(text.id), 'duration_ms': 60_000, 'typed_chars': 300, 'errors': 0}
    payload.update(kw)
    return c.post(f'{BASE}runs/', payload, content_type='application/json')


class TestScoring:
    def test_exact_wpm_and_accuracy(self):
        # 280 correct of 300 typed in 60 s: 280/5 = 56 wpm, 280/300 = 93.33 %
        assert score_run(text_length=400, duration_ms=60_000, typed_chars=300, errors=20) == (
            280,
            Decimal('56.00'),
            Decimal('93.33'),
        )

    def test_half_minute(self):
        # 150 correct in 30 s = 30 words in 0.5 min = 60 wpm
        assert score_run(text_length=400, duration_ms=30_000, typed_chars=150, errors=0)[1:] == (
            Decimal('60.00'),
            Decimal('100.00'),
        )

    def test_rejects_short_duration(self):
        with pytest.raises(TypingRejected):
            score_run(text_length=400, duration_ms=4_999, typed_chars=20, errors=0)

    def test_five_seconds_is_accepted(self):
        score_run(text_length=400, duration_ms=5_000, typed_chars=20, errors=0)

    def test_rejects_over_250_wpm(self):
        # 1300 correct chars in 60 s = 260 wpm
        with pytest.raises(TypingRejected):
            score_run(text_length=2000, duration_ms=60_000, typed_chars=1300, errors=0)

    def test_250_wpm_is_accepted(self):
        assert score_run(text_length=2000, duration_ms=60_000, typed_chars=1250, errors=0)[
            1
        ] == Decimal('250.00')

    def test_gross_speed_also_capped(self):
        # only 100 correct, but 1300 keystrokes in 60 s: key mashing
        with pytest.raises(TypingRejected):
            score_run(text_length=2000, duration_ms=60_000, typed_chars=1300, errors=1200)

    def test_rejects_inconsistent_summary(self):
        with pytest.raises(TypingRejected):
            score_run(text_length=400, duration_ms=60_000, typed_chars=10, errors=11)
        with pytest.raises(TypingRejected):  # more correct chars than the text has
            score_run(text_length=100, duration_ms=60_000, typed_chars=300, errors=0)

    def test_rejects_too_long(self):
        with pytest.raises(TypingRejected):
            score_run(text_length=400, duration_ms=900_001, typed_chars=100, errors=0)


class TestApi:
    def test_client_numbers_are_ignored(self, u, text):
        r = _run(client(u), text, errors=20, wpm=999, accuracy=100, correct_chars=300)
        assert r.status_code == 201
        body = r.json()
        assert body['wpm'] == 56.0 and body['accuracy'] == 93.33
        assert body['correct_chars'] == 280
        run = TypingRun.objects.get()
        assert run.wpm == Decimal('56.00')

    def test_short_run_rejected_and_not_stored(self, u, text):
        r = _run(client(u), text, duration_ms=3_000, typed_chars=20)
        assert r.status_code == 400
        assert TypingRun.objects.count() == 0

    def test_implausible_run_rejected(self, u, text):
        r = _run(client(u), text, typed_chars=400, duration_ms=5_000)  # 96 wpm? 400/5/(1/12)=960
        assert r.status_code == 400 and TypingRun.objects.count() == 0

    def test_requires_auth(self, text):
        from django.test import Client

        r = Client().post(f'{BASE}runs/', {}, content_type='application/json')
        assert r.status_code in (401, 403)

    def test_inactive_text_rejected(self, u, text):
        text.is_active = False
        text.save()
        assert _run(client(u), text).status_code == 404

    def test_personal_best_flag_and_best_endpoint(self, u, text):
        c = client(u)
        assert _run(c, text, typed_chars=200).json()['is_personal_best'] is True  # 40 wpm
        assert _run(c, text, typed_chars=150).json()['is_personal_best'] is False  # 30 wpm
        assert _run(c, text, typed_chars=300).json()['is_personal_best'] is True  # 60 wpm
        best = c.get(f'{BASE}best/', {'language': 'en'}).json()
        assert best['best']['wpm'] == 60.0 and best['runs_count'] == 3

    def test_sloppy_run_never_counts_as_best(self, u, text):
        # 80 % accuracy: 200 typed, 40 errors, 160 correct -> 32 wpm but below the 90 % floor
        r = _run(client(u), text, typed_chars=200, errors=40).json()
        assert r['accuracy'] == 80.0 and r['is_personal_best'] is False
        assert client(u).get(f'{BASE}best/', {'language': 'en'}).json()['best'] is None

    def test_history_is_own_only_and_filtered(self, u, text, uz, org):
        other = member(org, 'other', 'student')
        _run(client(u), text)
        _run(client(u), uz)
        _run(client(other), text)
        all_runs = client(u).get(f'{BASE}runs/').json()
        assert len(all_runs) == 2
        uz_runs = client(u).get(f'{BASE}runs/', {'language': 'uz-Latn'}).json()
        assert [r['language'] for r in uz_runs] == ['uz-Latn']

    def test_texts_by_language(self, u, text, uz):
        rows = client(u).get(f'{BASE}texts/', {'language': 'uz-Latn'}).json()
        assert 'U' in [r['title'] for r in rows]
        assert {r['language'] for r in rows} == {'uz-Latn'}
        assert client(u).get(f'{BASE}texts/', {'language': 'klingon'}).status_code == 400

    def test_seeded_texts_cover_all_languages(self, db):
        langs = set(TypingText.objects.values_list('language', flat=True))
        assert langs >= {'uz-Latn', 'uz-Cyrl', 'ru', 'en', 'kaa'}


class TestLeaderboard:
    def test_per_language_best_wpm_and_rank(self, db, org, text, uz):
        users = [member(org, f'p{i}', 'student') for i in range(3)]
        _run(client(users[0]), text, typed_chars=300)  # 60 wpm
        _run(client(users[0]), text, typed_chars=200)  # 40 wpm, same user: best stays 60
        _run(client(users[1]), text, typed_chars=400, duration_ms=60_000)  # 80 wpm
        _run(client(users[2]), uz, typed_chars=350)  # other language
        body = client(users[0]).get(f'{BASE}leaderboard/', {'language': 'en'}).json()
        assert [(e['rank'], e['username'], e['best_wpm']) for e in body['entries']] == [
            (1, 'p1', 80.0),
            (2, 'p0', 60.0),
        ]
        assert body['me'] == {'rank': 2, 'best_wpm': 60.0}

    def test_sloppy_runs_excluded_from_board(self, db, org, text):
        cheat = member(org, 'sloppy', 'student')
        _run(client(cheat), text, typed_chars=400, errors=100)  # 60 wpm but 75 % accuracy
        body = client(cheat).get(f'{BASE}leaderboard/', {'language': 'en'}).json()
        assert body['entries'] == [] and body['me'] is None

    def test_language_required(self, u):
        assert client(u).get(f'{BASE}leaderboard/').status_code == 400
