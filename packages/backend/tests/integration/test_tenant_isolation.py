"""
Integration tests for multi-tenant data isolation
Focus: TenantManager enforcement, context switching, global manager bypass
"""

import pytest

from apps.catalog.models import Question
from core.tenant import clear_current_org, set_current_org


@pytest.mark.integration
class TestTenantIsolation:
    """Verify complete data isolation between tenants."""

    def test_tenant_manager_isolates_org_data(self, db, org, org2, subject):
        """Org1 user querying → can't see Org2 data."""
        q1 = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q2 = Question.objects.create(
            organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
        )

        set_current_org(org)
        assert Question.objects.count() == 1
        assert Question.objects.first() == q1

        set_current_org(org2)
        assert Question.objects.count() == 1
        assert Question.objects.first() == q2

    def test_global_manager_sees_all_orgs(self, db, org, org2, subject):
        """global_objects bypasses tenant context."""
        q1 = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q2 = Question.objects.create(
            organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
        )

        set_current_org(org)
        # global_objects ignores context
        assert Question.global_objects.count() == 2

    def test_context_switch_changes_queryset(self, db, org, org2, subject):
        """Switch context → queryset changes immediately."""
        q1 = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q2 = Question.objects.create(
            organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
        )

        set_current_org(org)
        qs1 = Question.objects.all()
        assert len(qs1) == 1

        set_current_org(org2)
        qs2 = Question.objects.all()
        assert len(qs2) == 1
        assert qs1.first() != qs2.first()

    def test_for_org_explicit_filter(self, db, org, org2, subject):
        """objects.for_org(org) filters explicitly regardless of context."""
        q1 = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q2 = Question.objects.create(
            organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
        )

        set_current_org(org)
        # Explicit for_org ignores context
        assert Question.objects.for_org(org2).count() == 1
        assert Question.objects.for_org(org2).first() == q2

    def test_no_context_returns_empty_fail_closed(self, db, org, org2, subject):
        """
        Fail-closed: context=None va explicit unscoped yo'q → bo'sh queryset.
        (Lesson 11 — defense-in-depth, eski "no context = unfiltered" xavfli edi.)
        """
        from core.tenant import set_current_org, unscoped_context

        # Org context bilan yaratamiz (TenantManager fail-closed bo'lsa create ishlamaydi)
        set_current_org(org)
        q1 = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        set_current_org(org2)
        q2 = Question.objects.create(
            organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
        )

        clear_current_org()
        # Default fail-closed: no context → empty
        assert Question.objects.count() == 0

        # Explicit unscoped: barchasi ko'rinadi (admin/worker pattern)
        with unscoped_context():
            assert Question.objects.count() == 2

        # global_objects ham barchasi ko'rinadi (eski escape hatch)
        assert Question.global_objects.count() == 2
