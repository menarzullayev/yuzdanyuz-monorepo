"""Content plan: per-subject progress of the approved question bank against its target."""

from django.db.models import Count, Exists, F, OuterRef, Q

from apps.catalog.models import ContentTarget, Question, QuestionVersion, Subject

DEFAULT_TARGET_QUESTIONS = 300
DEFAULT_TARGET_VARIANTS = 10
CONTENT_LANGUAGES = ('uz', 'ru')

READY, LOW, EMPTY = 'ready', 'low', 'empty'
STATUSES = (READY, LOW, EMPTY)

_SUBJECT_PATH = 'question_links__question_version__question__subject_id'


def compute_status(count: int, target: int) -> str:
    """READY when count >= target (so target 0 is always READY), EMPTY at 0, else LOW."""
    if count >= target:
        return READY
    if count == 0:
        return EMPTY
    return LOW


def approved_questions():
    """Questions that count as approved: active and with a live (non-quarantined) current version.

    Questions only exist once a QuestionDraft is published, so drafts never count.
    """
    live_version = QuestionVersion.objects.filter(
        question=OuterRef('pk'),
        version_number=OuterRef('current_version'),
        is_quarantined=False,
    )
    return Question.objects.filter(is_active=True).filter(Exists(live_version))


def _variant_counts():
    """Published mock exams (variants) that contain at least one question of the subject."""
    from apps.exams.models import MockExam

    rows = (
        MockExam.objects.filter(status=MockExam.Status.PUBLISHED)
        .annotate(subject_id=F(_SUBJECT_PATH))
        .values('subject_id')
        .annotate(n=Count('id', distinct=True))
        .order_by()
    )
    return {r['subject_id']: r['n'] for r in rows if r['subject_id']}


def build_plan(org, *, status=None, search=None):
    """Return {'summary': {...}, 'results': [...]} for subjects visible to `org`.

    org=None means platform staff without an org: all subjects, default targets.
    Summary counters always cover every visible subject; `status`/`search` only filter results.
    """
    subjects = Subject.objects.filter(is_active=True)
    if org is not None:
        subjects = subjects.filter(Q(organization__isnull=True) | Q(organization=org))
    subjects = subjects.order_by('name')

    per_lang = {}
    for row in (
        approved_questions().values('subject_id', 'language').annotate(n=Count('id')).order_by()
    ):
        per_lang.setdefault(row['subject_id'], {})[row['language']] = row['n']

    targets = {}
    if org is not None:
        targets = {t.subject_id: t for t in ContentTarget.objects.filter(organization=org)}
    variants = _variant_counts()

    results = []
    counters = dict.fromkeys(STATUSES, 0)
    for subject in subjects:
        langs = per_lang.get(subject.id, {})
        total = sum(langs.values())
        t = targets.get(subject.id)
        target_q = t.target_questions if t else DEFAULT_TARGET_QUESTIONS
        target_v = t.target_variants if t else DEFAULT_TARGET_VARIANTS
        st = compute_status(total, target_q)
        counters[st] += 1
        results.append(
            {
                'subject_id': subject.id,
                'subject_name': subject.name,
                'target_questions': target_q,
                'target_variants': target_v,
                'has_custom_target': t is not None,
                'approved_questions': total,
                'approved_by_language': langs,
                'missing_languages': [c for c in CONTENT_LANGUAGES if not langs.get(c)],
                'variants': variants.get(subject.id, 0),
                'status': st,
            }
        )

    summary = {'total': len(results), **counters}
    summary['label'] = (
        f'{summary["total"]} total · {counters[READY]} ready · '
        f'{counters[LOW]} low · {counters[EMPTY]} empty'
    )
    if status:
        results = [r for r in results if r['status'] == status]
    if search:
        needle = search.lower()
        results = [r for r in results if needle in r['subject_name'].lower()]
    return {'summary': summary, 'results': results}
