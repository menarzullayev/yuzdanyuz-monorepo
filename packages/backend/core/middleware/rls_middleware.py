"""
PostgreSQL Row Level Security (RLS) Middleware
Sets organization context for database-level multi-tenant isolation
"""

import logging

from django.conf import settings
from django.db import connection
from django.http import HttpResponseServerError
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


class RLSMiddleware(MiddlewareMixin):
    """
    Set PostgreSQL session variables for RLS policies.

    Usage:
    1. Enable in settings.MIDDLEWARE
    2. Assumes request.org is set by TenantMiddleware
    3. Automatically filters tenant-aware queries

    Defense-in-depth: agar RLS session variable o'rnatib bo'lmasa,
    request to'xtaydi (HTTP 500). L3 himoya qatlami yumshoq xato bilan
    o'tib ketishi xavfli (L1/L2 yetarli emas degan tushuncha).
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
                    cursor.execute('SET app.current_org_id = %s;', [org_id])
                    logger.debug(f'RLS: Set org context to {org_id}')
                else:
                    # No org context (anonymous or platform admin)
                    cursor.execute('RESET app.current_org_id;')
                    logger.debug('RLS: Cleared org context (platform admin)')

                # ISSUE-110 W6: ilgari `SET app.is_admin = ...` chaqirilardi, lekin
                # hech bir RLS policy uni o'qimasdi. Dead code olib tashlandi.
                # Superuser/staff bypass app qatlamida `TenantManager`'ning
                # `unscoped_context()` yoki `is_unscoped_allowed()` orqali hal qilinadi.

        except Exception as e:
            logger.error('RLS middleware error: %s', e, exc_info=True)
            # Defense-in-depth: L3 (RLS) muvaffaqiyatsiz bo'lsa, request rad etiladi.
            # Yumshoq xato bilan o'tib ketish L1/L2 qatlamlariga to'liq ishonib qoladi.
            return HttpResponseServerError('Database security context error')

        return None

    @staticmethod
    def _is_rls_enabled():
        """Check if RLS is enabled in settings"""
        return getattr(settings, 'ENABLE_RLS', True)
