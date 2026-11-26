"""Profile the hot student path (audit G09): start a 40-question mock, answer every
question, submit, read the result. Prints per-endpoint latency, SQL query counts and the
top cumulative functions. Disposable database only (creates its own org, user and mock).

    python manage.py migrate && python manage.py shell < scripts/profile_exam_flow.py
"""

import cProfile
import io
import pstats
import statistics
import time
import uuid
from datetime import timedelta

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.catalog.models import Question, QuestionVersion, Subject
from apps.exams.models import MockExam, MockExamQuestion
from apps.organizations.models import Membership, Organization, OrgRole
from core.tenant import tenant_context

N = 40
tag = uuid.uuid4().hex[:6]
org = Organization.objects.create(name=f'Profile {tag}', slug=f'profile-{tag}')
user = CustomUser.objects.create_user(
    username=f'prof{tag}', email=f'p{tag}@x.uz', password='x' * 16
)
m = Membership.objects.create(
    user=user,
    organization=org,
    role=OrgRole.objects.get(organization=org, name='student'),
    status='active',
    is_primary=True,
)
m.activate()
subject, _ = Subject.objects.get_or_create(slug='profile', defaults={'name': 'Profile'})
now = timezone.now()
with tenant_context(org):
    mock = MockExam.objects.create(
        organization=org,
        title='Profile',
        duration_minutes=60,
        created_by=user,
        scheduled_at=now - timedelta(minutes=1),
        closes_at=now + timedelta(hours=2),
        status=MockExam.Status.PUBLISHED,
    )
    qvs = []
    for i in range(N):
        q = Question.objects.create(organization=org, subject=subject, type='SC')
        qv = QuestionVersion.objects.create(
            question=q,
            version_number=1,
            content={'text': f'Q{i}'},
            options=[{'id': 1, 'text': 'A', 'is_correct': True}, {'id': 2, 'text': 'B'}],
        )
        MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=i, points=1)
        qvs.append(qv)

c = Client(SERVER_NAME='localhost')
c.force_login(user)
timings: dict[str, list[float]] = {}
queries: dict[str, list[int]] = {}


def call(name, method, url, **kw):
    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter()
        resp = getattr(c, method)(url, **kw)
        dt = (time.perf_counter() - t0) * 1000
    # The result endpoint answers 403 onboarding_required for this bare user; still timed.
    assert resp.status_code < 400 or name == 'result', (name, resp.status_code, resp.content[:200])
    timings.setdefault(name, []).append(dt)
    queries.setdefault(name, []).append(len(ctx.captured_queries))
    return resp


prof = cProfile.Profile()
prof.enable()
attempt = call('start', 'post', f'/api/exams/mocks/{mock.id}/start/').json()['attempt']['id']
for qv in qvs:
    call(
        'answer',
        'post',
        f'/api/exams/attempts/{attempt}/answer/',
        data={'question_version': str(qv.id), 'selected': {'id': 1}},
        content_type='application/json',
    )
call('submit', 'post', f'/api/exams/attempts/{attempt}/submit/')
call('result', 'get', f'/api/exams/attempts/{attempt}/')
prof.disable()

print('| endpoint | calls | mean ms | p95 ms | SQL queries/call |')
print('|---|---|---|---|---|')
for name, values in timings.items():
    p95 = sorted(values)[max(0, int(len(values) * 0.95) - 1)]
    print(
        f'| {name} | {len(values)} | {statistics.mean(values):.1f} | {p95:.1f} | '
        f'{statistics.mean(queries[name]):.1f} |'
    )

buf = io.StringIO()
pstats.Stats(prof, stream=buf).sort_stats('cumulative').print_stats(400)
lines = [
    ln
    for ln in buf.getvalue().splitlines()
    if '/work/r/packages/backend/' in ln and '/middleware' not in ln
]
print('\nTop cumulative (project + ORM):')
for ln in lines[:25]:
    print(ln.replace('/work/r/packages/backend/', ''))
