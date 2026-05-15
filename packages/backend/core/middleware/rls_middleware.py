"""
PostgreSQL Row Level Security (RLS) Middleware
Sets organization context for database-level multi-tenant isolation
"""
import logging
from django.utils.deprecation import MiddlewareMixin
from django.db import connection
from django.conf import settings

logger = logging.getLogger(__name__)


class RLSMiddleware(MiddlewareMixin):
    """
    Set PostgreSQL session variables for RLS policies.

    Usage:
    1. Enable in settings.MIDDLEWARE
    2. Assumes request.org is set by TenantMiddleware
    3. Automatically filters tenant-aware queries
    """

    def process_request(self, request):
        """Set organization context for RLS policies"""
        if not self._is_rls_enabled():
            return None

        # Get organization from request (set by TenantMiddleware)
        org = getattr(request, 'org', None)

        try:
            with connection.cursor() as cursor:
                # Set organization context for RLS policies
                if org:
                    org_id = str(org.id)
                    cursor.execute("SET app.current_org_id = %s;", [org_id])
                    logger.debug(f"RLS: Set org context to {org_id}")
                else:
                    # No org context (anonymous or platform admin)
                    cursor.execute("SET app.current_org_id = NULL;")
                    logger.debug("RLS: Cleared org context (platform admin)")

                # Mark superuser for RLS policy bypass
                if request.user and request.user.is_authenticated:
                    is_admin = request.user.is_superuser or request.user.is_staff
                    cursor.execute(
                        "SET app.is_admin = %s;",
                        [str(is_admin).lower()]
                    )

        except Exception as e:
            logger.warning(f"RLS middleware error: {e}")
            # Don't fail request if RLS setup fails
            pass

        return None

    @staticmethod
    def _is_rls_enabled():
        """Check if RLS is enabled in settings"""
        return getattr(settings, 'ENABLE_RLS', True)
