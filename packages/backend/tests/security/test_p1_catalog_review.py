"""B02 — import-review endpoints require catalog:edit in the batch's organization."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import ImportBatch, Question, QuestionDraft, Subject
from apps.organizations.models import Membership, MembershipStatus, OrgRole
from core.tenant import tenant_context, unscoped_context

pytestmark = [pytest.mark.security, pytest.mark.django_db]


def _batch(org, subject):
    with tenant_context(org):
        batch = ImportBatch.objects.create(organization=org, file_type='csv')
    draft = QuestionDraft.objects.create(
        batch=batch,
        subject=subject,
        data={
            'text': 'Savol',
            'options': [
                {'text': 'A', 'is_correct': True},
                {'text': 'B', 'is_correct': False},
            ],
        },
        is_valid=True,
    )
    return batch, draft


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _bulk(client, batch, draft, action):
    return client.post(
        reverse('catalog:draft_bulk_action_htmx', args=[batch.id]),
        {'action': action, 'draft_ids': [str(draft.id)]},
    )


def _join(user, org, role):
    m = Membership.objects.create(
        user=user,
        organization=org,
        role=OrgRole.objects.get(organization=org, name=role),
        status=MembershipStatus.ACTIVE,
        is_primary=True,
    )
    m.activate()


class TestStudentCannotEditQuestionBank:
    def test_student_cannot_bulk_delete(self, org, user, member, subject):
        batch, draft = _batch(org, subject)
        resp = _bulk(_client(user), batch, draft, 'delete')
        assert resp.status_code == 403
        assert QuestionDraft.objects.filter(pk=draft.pk).exists()

    def test_student_cannot_bulk_publish(self, org, user, member, subject):
        batch, draft = _batch(org, subject)
        resp = _bulk(_client(user), batch, draft, 'publish')
        assert resp.status_code == 403
        with unscoped_context():
            assert not Question.objects.exists()

    def test_student_cannot_autosave(self, org, user, member, subject):
        batch, draft = _batch(org, subject)
        resp = _client(user).post(
            reverse('catalog:draft_autosave_htmx', args=[draft.id]), {'text': 'hacked'}
        )
        assert resp.status_code == 403
        draft.refresh_from_db()
        assert draft.data['text'] == 'Savol'

    @pytest.mark.parametrize('name', ['review_dashboard', 'draft_list_htmx'])
    def test_student_cannot_read_review_pages(self, org, user, member, subject, name):
        batch, _ = _batch(org, subject)
        assert _client(user).get(reverse(f'catalog:{name}', args=[batch.id])).status_code == 403

    def test_student_cannot_read_draft(self, org, user, member, subject):
        _, draft = _batch(org, subject)
        resp = _client(user).get(reverse('catalog:draft_detail_htmx', args=[draft.id]))
        assert resp.status_code == 403

    def test_user_without_organization_cannot_touch_any_batch(self, org, user2, subject):
        batch, draft = _batch(org, subject)
        assert _bulk(_client(user2), batch, draft, 'delete').status_code in (403, 404)
        assert QuestionDraft.objects.filter(pk=draft.pk).exists()

    def test_teacher_of_another_org_cannot_touch_batch(self, org, org2, user2, subject):
        _join(user2, org2, 'teacher')
        batch, draft = _batch(org, subject)
        assert _bulk(_client(user2), batch, draft, 'delete').status_code == 404
        assert QuestionDraft.objects.filter(pk=draft.pk).exists()


class TestEditorsKeepAccess:
    def test_teacher_can_publish(self, org, user, teacher_member, subject):
        batch, draft = _batch(org, subject)
        resp = _bulk(_client(user), batch, draft, 'publish')
        assert resp.status_code == 200
        draft.refresh_from_db()
        assert draft.is_published

    def test_teacher_can_autosave(self, org, user, teacher_member, subject):
        _, draft = _batch(org, subject)
        resp = _client(user).post(
            reverse('catalog:draft_autosave_htmx', args=[draft.id]), {'text': 'Yangi'}
        )
        assert resp.status_code == 200
        draft.refresh_from_db()
        assert draft.data['text'] == 'Yangi'

    def test_autosave_ignores_subject_of_another_org(
        self, org, org2, user, teacher_member, subject
    ):
        foreign = Subject.objects.create(name='Private', slug='private-x', organization=org2)
        _, draft = _batch(org, subject)
        _client(user).post(
            reverse('catalog:draft_autosave_htmx', args=[draft.id]), {'subject_id': str(foreign.id)}
        )
        draft.refresh_from_db()
        assert draft.subject_id == subject.id
