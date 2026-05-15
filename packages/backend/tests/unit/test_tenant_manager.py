"""
Unit tests for core/managers.py and core/mixins.py
"""

import pytest

from apps.catalog.models import Question
from core.tenant import clear_current_org, set_current_org


@pytest.mark.unit
class TestTenantManager:
    """TenantManager filters by current_org."""

    def test_tenant_manager_filters_by_current_org(self, db, org, org2, subject):
        """set_current_org(org) → only that org's objects returned."""
        # Create questions in both orgs
        q1 = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q2 = Question.objects.create(
            organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
        )

        # Query without context → both visible
        assert Question.global_objects.count() == 2

        # Query with org1 context
        set_current_org(org)
        assert Question.objects.count() == 1
        assert Question.objects.first() == q1

        # Query with org2 context
        set_current_org(org2)
        assert Question.objects.count() == 1
        assert Question.objects.first() == q2

    def test_tenant_manager_no_filter_without_context(self, db, org, org2, subject):
        """org=None context → unfiltered queryset."""
        q1 = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q2 = Question.objects.create(
            organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
        )

        clear_current_org()
        # No filtering when context is None
        assert Question.objects.count() == 2

    def test_global_manager_bypasses_filter(self, db, org, org2, subject):
        """global_objects always returns all regardless of context."""
        q1 = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q2 = Question.objects.create(
            organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
        )

        set_current_org(org)
        # global_objects ignores context
        assert Question.global_objects.count() == 2

    def test_tenant_manager_for_org(self, db, org, org2, subject):
        """objects.for_org(org) filters by explicit org."""
        q1 = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q2 = Question.objects.create(
            organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
        )

        # Explicit for_org
        assert Question.objects.for_org(org).count() == 1
        assert Question.objects.for_org(org).first() == q1

        assert Question.objects.for_org(org2).count() == 1
        assert Question.objects.for_org(org2).first() == q2

    def test_tenant_mixin_has_organization_fk(self, org, subject):
        """Tenant-inheriting model has organization FK."""
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        assert q.organization == org
        assert hasattr(q, 'organization')
        assert hasattr(Question, 'objects')  # TenantManager
        assert hasattr(Question, 'global_objects')  # GlobalManager
