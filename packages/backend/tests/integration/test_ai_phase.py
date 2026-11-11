"""AI phase: admin provider API, grading queue (AI estimate -> final score),
AI/teacher calibration report and the speaking pipeline."""

import json
from datetime import timedelta

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import AIProviderConfig, Question, QuestionVersion
from apps.intelligence import grading, tasks
from apps.intelligence.models import OpenEndedSubmission
from apps.organizations.models import Membership, MembershipStatus, OrgRole
from core.tenant import tenant_context

User = get_user_model()
pytestmark = pytest.mark.integration
SECRET = 'sk-live-ZZZZ9876'


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _member(org, username, role):
    u = User.objects.create_user(username=username, email=f'{username}@e.com', password='pass123')
    m = Membership.objects.create(
        user=u,
        organization=org,
        role=OrgRole.objects.get(organization=org, name=role),
        status=MembershipStatus.ACTIVE,
        is_primary=True,
    )
    m.activate()
    return u


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _post(client, url, data):
    return client.post(url, data=json.dumps(data), content_type='application/json')


def _qv(org, subject):
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.OPEN_ENDED
        )
        return QuestionVersion.objects.create(
            question=q, version_number=1, content={'text': 'Describe your city.'}, options=[]
        )


def _sub(student, qv, **kw):
    defaults = {
        'user': student,
        'question_version': qv,
        'submission_type': 'essay',
        'content': 'My city is big.',
        'status': OpenEndedSubmission.Status.AI_REVIEWED,
        'ai_score': 70,
        'ai_feedback': {'grammar': 70, 'content': 70, 'structure': 70, 'relevance': 70},
    }
    defaults.update(kw)
    return OpenEndedSubmission.objects.create(**defaults)


@pytest.fixture
def world(db, org, org2, subject):
    return {
        'student': _member(org, 'stu1', 'student'),
        'teacher': _member(org, 'tea1', 'teacher'),
        'observer': _member(org, 'obs1', 'observer'),
        'teacher2': _member(org2, 'tea2', 'teacher'),
        'staff': User.objects.create_user(
            username='staff1', email='s@e.com', password='pass123', is_staff=True
        ),
        'qv': _qv(org, subject),
        'qv2': _qv(org2, subject),
    }


# ─── Admin provider API ──────────────────────────────────────────────────────


class TestProviderAdminApi:
    def _payload(self, **kw):
        base = {
            'name': 'GPT',
            'provider': 'openai',
            'model': 'gpt-4o',
            'api_key': SECRET,
            'profile': 'writing',
        }
        return {**base, **kw}

    def test_staff_creates_and_key_is_never_returned(self, world):
        c = _client(world['staff'])
        resp = _post(c, reverse('intelligence:ai-providers'), self._payload())
        assert resp.status_code == 201, resp.content
        assert SECRET not in resp.content.decode()
        body = resp.json()
        assert 'api_key' not in body
        assert body['api_key_last4'] == SECRET[-4:]
        assert body['profile'] == 'writing'

        for url in (
            reverse('intelligence:ai-providers'),
            reverse('intelligence:ai-provider-detail', args=[body['id']]),
        ):
            assert SECRET not in c.get(url).content.decode()
        raw = AIProviderConfig.objects.get(pk=body['id']).api_key
        assert SECRET not in raw

    def test_teacher_and_student_are_forbidden(self, world):
        for who in ('teacher', 'student', 'observer'):
            c = _client(world[who])
            assert c.get(reverse('intelligence:ai-providers')).status_code == 403
            assert (
                _post(c, reverse('intelligence:ai-providers'), self._payload()).status_code == 403
            )
        assert not AIProviderConfig.objects.exists()

    def test_patch_without_key_keeps_it_and_empty_key_clears_it(self, world):
        c = _client(world['staff'])
        pk = _post(c, reverse('intelligence:ai-providers'), self._payload()).json()['id']
        url = reverse('intelligence:ai-provider-detail', args=[pk])
        resp = c.patch(
            url, data=json.dumps({'model': 'gpt-4o-mini'}), content_type='application/json'
        )
        assert resp.status_code == 200
        assert AIProviderConfig.objects.get(pk=pk).get_api_key() == SECRET
        # clearing a key a provider needs is rejected
        resp = c.patch(url, data=json.dumps({'api_key': ''}), content_type='application/json')
        assert resp.status_code == 400

    def test_key_is_required_for_hosted_providers_but_not_ollama(self, world):
        c = _client(world['staff'])
        url = reverse('intelligence:ai-providers')
        assert _post(c, url, self._payload(api_key='')).status_code == 400
        ok = _post(
            c, url, self._payload(provider='ollama', api_key='', base_url='http://gpu:11434')
        )
        assert ok.status_code == 201

    def test_validation(self, world):
        c = _client(world['staff'])
        url = reverse('intelligence:ai-providers')
        assert _post(c, url, self._payload(base_url='ftp://x')).status_code == 400
        assert _post(c, url, self._payload(profile='nope')).status_code == 400
        bad_stt = self._payload(provider='gemini', extra_options={'purpose': 'stt'})
        assert _post(c, url, bad_stt).status_code == 400

    def test_default_profile_maps_to_all(self, world):
        c = _client(world['staff'])
        body = _post(
            c, reverse('intelligence:ai-providers'), self._payload(profile='default')
        ).json()
        assert body['profile'] == 'default'
        assert AIProviderConfig.objects.get(pk=body['id']).use_for == 'all'

    def test_test_connection_endpoint(self, world, monkeypatch):
        c = _client(world['staff'])
        pk = _post(c, reverse('intelligence:ai-providers'), self._payload()).json()['id']
        url = reverse('intelligence:ai-provider-test', args=[pk])

        def ok(url, **kw):
            return httpx.Response(
                200,
                json={'choices': [{'message': {'content': 'ok'}}]},
                request=httpx.Request('POST', url),
            )

        monkeypatch.setattr(httpx, 'post', ok)
        assert c.post(url).json()['ok'] is True

        def bad(url, **kw):
            return httpx.Response(401, json={'e': SECRET}, request=httpx.Request('POST', url))

        monkeypatch.setattr(httpx, 'post', bad)
        resp = c.post(url)
        assert resp.json()['ok'] is False
        assert SECRET not in resp.content.decode()
        # not for teachers
        assert _client(world['teacher']).post(url).status_code == 403


# ─── Grading queue ───────────────────────────────────────────────────────────


class TestGradingQueue:
    def test_student_and_anonymous_are_forbidden(self, world):
        sub = _sub(world['student'], world['qv'])
        urls = [
            reverse('intelligence:grading-queue'),
            reverse('intelligence:grading-next'),
            reverse('intelligence:grading-detail', args=[sub.id]),
            reverse('intelligence:grading-calibration'),
        ]
        for url in urls:
            assert _client(world['student']).get(url).status_code == 403
            assert Client().get(url).status_code == 403
        grade = reverse('intelligence:grading-grade', args=[sub.id])
        assert _post(_client(world['student']), grade, {'score': 100}).status_code == 403
        sub.refresh_from_db()
        assert sub.human_score is None

    def test_observer_can_see_calibration_but_not_grade(self, world):
        sub = _sub(world['student'], world['qv'])
        c = _client(world['observer'])
        assert c.get(reverse('intelligence:grading-calibration')).status_code == 200
        assert c.get(reverse('intelligence:grading-queue')).status_code == 403
        grade = reverse('intelligence:grading-grade', args=[sub.id])
        assert _post(c, grade, {'score': 50}).status_code == 403

    def test_teacher_sees_pending_with_ai_estimate(self, world):
        _sub(world['student'], world['qv'])
        c = _client(world['teacher'])
        body = c.get(reverse('intelligence:grading-queue')).json()
        assert body['count'] == 1
        item = body['results'][0]
        assert item['ai_estimate']['is_estimate'] is True
        assert item['ai_estimate']['score'] == 70.0
        assert item['final'] is None

    def test_graded_items_leave_the_queue_and_final_overrides_estimate(self, world):
        sub = _sub(world['student'], world['qv'])
        c = _client(world['teacher'])
        grade = reverse('intelligence:grading-grade', args=[sub.id])
        resp = _post(c, grade, {'score': 55, 'criteria': {'grammar': 50}, 'comment': 'Weak'})
        assert resp.status_code == 200, resp.content
        assert resp.json()['final']['score'] == 55.0
        assert c.get(reverse('intelligence:grading-queue')).json()['count'] == 0

        sub.refresh_from_db()
        assert sub.status == OpenEndedSubmission.Status.HUMAN_APPROVED
        assert sub.human_reviewer == world['teacher']
        assert float(sub.ai_score) == 70.0  # the estimate is kept for calibration

        # the student now sees the final score, not the estimate
        view = _client(world['student']).get(
            reverse('intelligence:openended-detail', args=[sub.id])
        )
        score = view.json()['score']
        assert score == {'value': 55.0, 'is_final': True, 'label': 'final'}

    def test_student_sees_ai_score_labelled_as_estimate_until_final(self, world):
        sub = _sub(world['student'], world['qv'])
        view = _client(world['student']).get(
            reverse('intelligence:openended-detail', args=[sub.id])
        )
        assert view.json()['score'] == {'value': 70.0, 'is_final': False, 'label': 'AI estimate'}

    def test_score_defaults_to_criteria_mean(self, world):
        sub = _sub(world['student'], world['qv'])
        grade = reverse('intelligence:grading-grade', args=[sub.id])
        resp = _post(_client(world['teacher']), grade, {'criteria': {'grammar': 60, 'content': 80}})
        assert resp.json()['final']['score'] == 70.0

    @pytest.mark.parametrize(
        'payload',
        [
            {},
            {'score': 101},
            {'score': -1},
            {'score': 'abc'},
            {'score': True},
            {'score': 50, 'criteria': {'fluency': 50}},  # speaking criterion on a writing item
            {'score': 50, 'criteria': {'grammar': 500}},
            {'score': 50, 'criteria': ['grammar']},
            {'score': 50, 'comment': 'x' * 5001},
        ],
    )
    def test_invalid_grades_are_rejected(self, world, payload):
        sub = _sub(world['student'], world['qv'])
        grade = reverse('intelligence:grading-grade', args=[sub.id])
        assert _post(_client(world['teacher']), grade, payload).status_code == 400
        sub.refresh_from_db()
        assert sub.human_score is None

    def test_next_returns_oldest_and_honours_skip(self, world):
        a = _sub(world['student'], world['qv'], content='a')
        b = _sub(world['student'], world['qv'], content='b')
        c = _client(world['teacher'])
        url = reverse('intelligence:grading-next')
        first = c.get(url).json()
        assert first['remaining'] == 2
        assert first['item']['id'] == str(a.id)
        assert first['item']['content'] == 'a'
        assert c.get(url, {'skip': str(a.id)}).json()['item']['id'] == str(b.id)
        assert c.get(url, {'skip': f'{a.id},{b.id}'}).json()['item'] is None
        assert c.get(url, {'skip': 'not-a-uuid'}).status_code == 400

    def test_type_filter(self, world):
        _sub(world['student'], world['qv'])
        _sub(
            world['student'],
            world['qv'],
            submission_type='audio',
            status=OpenEndedSubmission.Status.AWAITING_HUMAN,
            ai_score=None,
            ai_feedback={'skipped': 'no_stt_provider'},
        )
        c = _client(world['teacher'])
        url = reverse('intelligence:grading-queue')
        assert c.get(url, {'type': 'writing'}).json()['count'] == 1
        speaking = c.get(url, {'type': 'speaking'}).json()
        assert speaking['count'] == 1
        assert speaking['results'][0]['ai_skipped'] == 'no_stt_provider'
        assert c.get(url, {'type': 'reading'}).status_code == 400

    # cross-tenant isolation
    def test_other_organization_is_invisible(self, world):
        mine = _sub(world['student'], world['qv'])
        theirs = _sub(world['student'], world['qv2'])
        c = _client(world['teacher2'])
        listed = c.get(reverse('intelligence:grading-queue')).json()
        assert [i['id'] for i in listed['results']] == [str(theirs.id)]
        assert c.get(reverse('intelligence:grading-detail', args=[mine.id])).status_code == 404
        grade = reverse('intelligence:grading-grade', args=[mine.id])
        assert _post(c, grade, {'score': 1}).status_code == 404
        mine.refresh_from_db()
        assert mine.human_score is None
        # asking for another org explicitly is refused
        other = c.get(
            reverse('intelligence:grading-queue'),
            {'org': str(world['qv'].question.organization_id)},
        )
        assert other.status_code == 403
        assert c.get(reverse('intelligence:grading-next')).json()['item']['id'] == str(theirs.id)

    def test_platform_staff_sees_all_and_can_filter_by_org(self, world, org):
        _sub(world['student'], world['qv'])
        _sub(world['student'], world['qv2'])
        c = _client(world['staff'])
        url = reverse('intelligence:grading-queue')
        assert c.get(url).json()['count'] == 2
        assert c.get(url, {'org': str(org.id)}).json()['count'] == 1
        assert c.get(url, {'org': 'zzz'}).status_code == 400


# ─── Calibration ─────────────────────────────────────────────────────────────


def _graded(world, ai_minus_human, human=70, **kw):
    ai = human + ai_minus_human
    return _sub(
        world['student'],
        world['qv'],
        status=OpenEndedSubmission.Status.HUMAN_APPROVED,
        ai_score=ai,
        ai_feedback={'grammar': ai, 'content': ai},
        human_score=human,
        human_criteria=kw.pop('human_criteria', {'grammar': human}),
        human_completed_at=kw.pop('completed', timezone.now()),
        **kw,
    )


class TestCalibration:
    def test_math_on_fixed_data(self):
        rows = (
            [
                {
                    'ai': 78,
                    'human': 70,
                    'ai_criteria': {'grammar': 78},
                    'human_criteria': {'grammar': 70},
                }
            ]
            * 20
            + [
                {
                    'ai': 86,
                    'human': 70,
                    'ai_criteria': {'grammar': 86},
                    'human_criteria': {'grammar': 70},
                }
            ]
            * 5
            + [
                {
                    'ai': 66,
                    'human': 70,
                    'ai_criteria': {'grammar': 66},
                    'human_criteria': {'grammar': 70},
                }
            ]
            * 5
        )
        report = grading.calibration_report(rows)
        assert report['status'] == 'ok'
        # diffs: 20 x +8, 5 x +16, 5 x -4
        assert report['overall'] == {
            'n': 30,
            'mean_diff': 7.33,
            'mean_abs_diff': 8.67,
            'agreement_rate': 0.8333,  # |diff| <= 10: 25 of 30
        }
        assert report['criteria']['grammar']['mean_diff'] == 7.33
        assert report['conclusion']['bias'] == 'ai_higher'
        assert 'anchoring' in report['caveat']
        assert report['tolerance'] == grading.CALIBRATION_TOLERANCE

    def test_ai_lower_and_aligned(self):
        def rows(d):
            return [{'ai': 70 + d, 'human': 70, 'ai_criteria': {}, 'human_criteria': {}}] * 30

        assert grading.calibration_report(rows(-9))['conclusion']['bias'] == 'ai_lower'
        assert grading.calibration_report(rows(1))['conclusion']['bias'] == 'aligned'

    def test_tolerance_boundary_is_inclusive(self):
        rows = [{'ai': 80, 'human': 70, 'ai_criteria': {}, 'human_criteria': {}}] * 30
        assert grading.calibration_report(rows)['overall']['agreement_rate'] == 1.0

    def test_fewer_than_30_pairs_gives_count_and_no_numbers(self):
        rows = [{'ai': 80, 'human': 70, 'ai_criteria': {}, 'human_criteria': {}}] * 29
        report = grading.calibration_report(rows)
        assert report['pairs'] == 29
        assert report['status'] == 'not_enough_data'
        assert report['message'] == 'not enough data'
        assert report['conclusion'] is None
        assert report['overall'] is None

    def test_criterion_with_too_few_pairs_is_withheld(self):
        rows = [
            {
                'ai': 70,
                'human': 70,
                'ai_criteria': {'grammar': 70},
                'human_criteria': {'grammar': 70},
            }
        ] * 30
        rows[0] = {
            **rows[0],
            'ai_criteria': {'grammar': 70, 'content': 90},
            'human_criteria': {'grammar': 70, 'content': 60},
        }
        report = grading.calibration_report(rows)
        assert report['criteria']['content'] == {'n': 1, 'status': 'not_enough_data'}
        assert report['criteria']['grammar']['n'] == 30

    def test_endpoint_counts_only_complete_in_window_pairs(self, world):
        for _ in range(30):
            _graded(world, 8)
        _graded(world, 8, completed=timezone.now() - timedelta(days=400))  # outside the window
        _sub(world['student'], world['qv'])  # AI only, no teacher score
        _sub(world['student'], world['qv2'])  # other organization
        c = _client(world['teacher'])
        body = c.get(reverse('intelligence:grading-calibration')).json()
        assert body['pairs'] == 30
        assert body['status'] == 'ok'
        assert body['overall']['mean_diff'] == 8.0

    def test_endpoint_with_29_pairs_is_not_enough_data(self, world):
        for _ in range(29):
            _graded(world, 8)
        body = _client(world['teacher']).get(reverse('intelligence:grading-calibration')).json()
        assert body['pairs'] == 29
        assert body['status'] == 'not_enough_data'

    def test_report_is_cached_for_ten_minutes(self, world):
        c = _client(world['teacher'])
        url = reverse('intelligence:grading-calibration')
        assert c.get(url).json()['pairs'] == 0
        for _ in range(30):
            _graded(world, 8)
        assert c.get(url).json()['pairs'] == 0  # still the cached report
        assert grading.CALIBRATION_CACHE_SECONDS == 600
        cache.clear()
        assert c.get(url).json()['pairs'] == 30

    def test_cache_is_scoped_per_organization(self, world):
        for _ in range(30):
            _graded(world, 8)
        assert (
            _client(world['teacher'])
            .get(reverse('intelligence:grading-calibration'))
            .json()['pairs']
            == 30
        )
        other = _client(world['teacher2']).get(reverse('intelligence:grading-calibration')).json()
        assert other['pairs'] == 0


# ─── Speaking pipeline ───────────────────────────────────────────────────────


class FakeApis:
    """Routes httpx.post by URL: STT transcription vs chat completions."""

    def __init__(self, monkeypatch, scores=None, transcript='I usually go to school by bus.'):
        self.calls = []
        self.scores = scores or {
            'fluency': 70,
            'lexical_resource': 60,
            'grammar': 80,
            'rationale': 'Clear.',
        }
        self.transcript = transcript
        monkeypatch.setattr(httpx, 'post', self._post)

    def _post(self, url, **kw):
        self.calls.append((url, kw))
        req = httpx.Request('POST', url)
        if url.endswith('/audio/transcriptions'):
            return httpx.Response(200, json={'text': self.transcript}, request=req)
        content = json.dumps(self.scores)
        return httpx.Response(
            200, json={'choices': [{'message': {'content': content}}]}, request=req
        )


def _speaking_configs():
    AIProviderConfig.objects.create(
        name='stt',
        provider='openai',
        model='whisper-1',
        api_key=SECRET,
        use_for='speaking',
        extra_options={'purpose': 'stt', 'language': 'en'},
    )
    AIProviderConfig.objects.create(
        name='llm',
        provider='openai',
        model='gpt-4o',
        api_key=SECRET,
        use_for='speaking',
    )


def _audio():
    return SimpleUploadedFile('answer.mp3', b'fake-audio-bytes', content_type='audio/mpeg')


def _submit_audio(world, **extra):
    return _client(world['student']).post(
        reverse('intelligence:openended-submit'),
        data={
            'question_version': str(world['qv'].id),
            'submission_type': 'audio',
            'audio': _audio(),
            **extra,
        },
    )


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)


class TestSpeaking:
    def test_full_flow_transcript_then_three_criteria(self, world, monkeypatch):
        _speaking_configs()
        api = FakeApis(monkeypatch)
        resp = _submit_audio(world)
        assert resp.status_code == 202, resp.content
        body = resp.json()
        assert body['status'] == 'ai_reviewed'
        assert body['content'] == 'I usually go to school by bus.'
        assert body['score'] == {'value': 70.0, 'is_final': False, 'label': 'AI estimate'}
        assert body['ai_not_assessed'] == ['pronunciation']

        feedback = body['ai_feedback']
        assert {'fluency', 'lexical_resource', 'grammar'} <= feedback.keys()
        assert 'pronunciation' not in feedback and 'content' not in feedback
        assert feedback['based_on'] == 'transcript'

        stt_url, stt_kw = api.calls[0]
        assert stt_url == 'https://api.openai.com/v1/audio/transcriptions'
        assert stt_kw['data']['model'] == 'whisper-1'
        assert stt_kw['data']['language'] == 'en'
        assert stt_kw['headers']['Authorization'] == f'Bearer {SECRET}'
        llm_kw = api.calls[1][1]['json']
        system = ' '.join(m['content'] for m in llm_kw['messages'] if m['role'] == 'system')
        assert 'BAHOLANMAYDI' in system  # the rubric says pronunciation is not assessed
        assert 'I usually go to school by bus.' in llm_kw['messages'][-1]['content']

    def test_no_stt_provider_waits_for_a_human(self, world, monkeypatch):
        AIProviderConfig.objects.create(
            name='llm', provider='openai', model='gpt-4o', api_key=SECRET, use_for='speaking'
        )
        api = FakeApis(monkeypatch)
        body = _submit_audio(world).json()
        assert body['status'] == 'awaiting_human'
        assert body['score'] is None
        assert body['ai_feedback']['skipped'] == 'no_stt_provider'
        assert api.calls == []  # neither STT nor the LLM was called

        # it shows up in the teacher queue with a clear reason and can be graded by hand
        c = _client(world['teacher'])
        item = c.get(reverse('intelligence:grading-queue'), {'type': 'speaking'}).json()['results'][
            0
        ]
        assert item['ai_skipped'] == 'no_stt_provider'
        assert item['ai_estimate'] is None
        grade = reverse('intelligence:grading-grade', args=[item['id']])
        done = _post(c, grade, {'criteria': {'fluency': 60, 'lexical_resource': 70, 'grammar': 80}})
        assert done.json()['final']['score'] == 70.0

    def test_stt_row_alone_is_not_an_llm(self, world, monkeypatch):
        """An STT-only row must never be used to score (negative)."""
        AIProviderConfig.objects.create(
            name='stt',
            provider='openai',
            model='whisper-1',
            api_key=SECRET,
            use_for='all',
            extra_options={'purpose': 'stt'},
        )
        FakeApis(monkeypatch)
        # no LLM row and the test settings backend has no API key -> the AI step fails cleanly
        body = _submit_audio(world).json()
        assert body['status'] == 'failed'

    def test_writing_does_not_use_stt_and_scores_four_criteria(self, world, monkeypatch):
        AIProviderConfig.objects.create(
            name='w', provider='openai', model='gpt-4o', api_key=SECRET, use_for='writing'
        )
        api = FakeApis(
            monkeypatch,
            scores={
                'grammar': 80,
                'content': 70,
                'structure': 60,
                'relevance': 90,
                'rationale': 'x',
            },
        )
        resp = _client(world['student']).post(
            reverse('intelligence:openended-submit'),
            data=json.dumps(
                {
                    'question_version': str(world['qv'].id),
                    'submission_type': 'essay',
                    'content': 'Hello',
                }
            ),
            content_type='application/json',
        )
        assert resp.json()['ai_score'] == 75.0
        assert all('audio' not in url for url, _ in api.calls)

    def test_llm_missing_a_criterion_fails_the_submission(self, world, monkeypatch):
        _speaking_configs()
        FakeApis(monkeypatch, scores={'fluency': 70, 'grammar': 80, 'rationale': 'x'})
        body = _submit_audio(world).json()
        assert body['status'] == 'failed'
        sub = OpenEndedSubmission.objects.get(pk=body['id'])
        assert 'lexical_resource' in sub.ai_feedback['error']

    def test_empty_transcript_fails(self, world, monkeypatch):
        _speaking_configs()
        FakeApis(monkeypatch, transcript='   ')
        assert _submit_audio(world).json()['status'] == 'failed'

    def test_transient_stt_error_is_retried_then_succeeds(self, world, monkeypatch, request):
        from core.celery import app as celery_app

        # eager apply() only re-applies on retry() when exceptions are not propagated
        before = celery_app.conf.task_eager_propagates
        celery_app.conf.task_eager_propagates = False
        request.addfinalizer(lambda: setattr(celery_app.conf, 'task_eager_propagates', before))
        _speaking_configs()
        api = FakeApis(monkeypatch)
        real = api._post
        state = {'failed_once': False}

        def flaky(url, **kw):
            if url.endswith('/audio/transcriptions') and not state['failed_once']:
                state['failed_once'] = True
                return httpx.Response(503, json={}, request=httpx.Request('POST', url))
            return real(url, **kw)

        monkeypatch.setattr(httpx, 'post', flaky)
        sub = OpenEndedSubmission.objects.create(
            user=world['student'],
            question_version=world['qv'],
            submission_type='audio',
            audio_file=_audio(),
        )
        tasks.evaluate_openended_submission.apply(args=[str(sub.id)])
        sub.refresh_from_db()
        assert state['failed_once']
        assert sub.status == OpenEndedSubmission.Status.AI_REVIEWED

    def test_provider_errors_get_the_timeout_retry_wait(self):
        from apps.catalog.services.ai_providers.base import TransientProviderError

        assert tasks.ai_retry_countdown(TransientProviderError('503')) == 120
        assert tasks.ai_retry_countdown(RuntimeError('401')) is None

    @pytest.mark.parametrize(
        'kwargs',
        [
            {'audio': SimpleUploadedFile('a.txt', b'x')},
            {'audio': SimpleUploadedFile('a.mp3', b'x' * (25 * 1024 * 1024 + 1))},
        ],
    )
    def test_audio_upload_validation(self, world, kwargs):
        resp = _client(world['student']).post(
            reverse('intelligence:openended-submit'),
            data={
                'question_version': str(world['qv'].id),
                'submission_type': 'audio',
                **kwargs,
            },
        )
        assert resp.status_code == 400
        assert not OpenEndedSubmission.objects.exists()

    def test_audio_without_file_or_transcript_is_rejected(self, world):
        resp = _client(world['student']).post(
            reverse('intelligence:openended-submit'),
            data={'question_version': str(world['qv'].id), 'submission_type': 'audio'},
        )
        assert resp.status_code == 400
