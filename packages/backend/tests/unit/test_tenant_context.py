"""
Unit tests for core/tenant.py — thread-local tenant context management
"""

import pytest
from core.tenant import get_current_org, set_current_org, clear_current_org, tenant_context


@pytest.mark.unit
class TestTenantContext:
    """Tenant context management via thread-locals."""

    def test_get_current_org_default_none(self):
        """Fresh thread → get_current_org() = None."""
        clear_current_org()
        assert get_current_org() is None

    def test_set_and_get_current_org(self, org):
        """set_current_org(org) → get_current_org() = org."""
        set_current_org(org)
        assert get_current_org() == org

    def test_clear_current_org(self, org):
        """set → clear → get = None."""
        set_current_org(org)
        clear_current_org()
        assert get_current_org() is None

    def test_tenant_context_manager(self, org):
        """with tenant_context(org): ... → org set inside block."""
        clear_current_org()

        with tenant_context(org):
            assert get_current_org() == org

        # After block
        assert get_current_org() is None

    def test_tenant_context_cleanup_on_exit(self, org, org2):
        """Nested context → restore previous value."""
        set_current_org(org)

        with tenant_context(org2):
            assert get_current_org() == org2

        # Restored to previous
        assert get_current_org() == org

    def test_tenant_context_nested(self, org, org2):
        """Multiple nested contexts work correctly."""
        with tenant_context(org):
            assert get_current_org() == org

            with tenant_context(org2):
                assert get_current_org() == org2

                with tenant_context(None):
                    assert get_current_org() is None

                # Back to org2
                assert get_current_org() == org2

            # Back to org
            assert get_current_org() == org

    def test_tenant_context_cleanup_on_exception(self, org):
        """Exception inside block → still cleanup."""
        set_current_org(None)

        try:
            with tenant_context(org):
                assert get_current_org() == org
                raise ValueError("test error")
        except ValueError:
            pass

        # Should be cleaned up
        assert get_current_org() is None

    def test_tenant_context_with_none(self):
        """tenant_context(None) temporarily unsets."""
        org = None
        clear_current_org()

        with tenant_context(org):
            assert get_current_org() is None
