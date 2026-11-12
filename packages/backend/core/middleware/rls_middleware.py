"""
PostgreSQL Row Level Security (RLS) Middleware

The RLS variable `app.current_org_id` is no longer set here with a session-level
`SET`: that leaked between pooled clients under PgBouncer transaction pooling
(audit E05). `core.db_rls` now attaches it, transaction-local, to every statement
issued inside a tenant context. This middleware only guarantees that the tenant
context matches `request.org` for the rest of the request.
"""

import logging

from django.conf import settings
from django.utils.deprecation import MiddlewareMixin

from core.tenant import get_current_org, set_current_org

logger = logging.getLogger(__name__)


class RLSMiddleware(MiddlewareMixin):
    """Keep the tenant context (and so the per-statement RLS variable) on request.org.

    Assumes request.org is set by TenantMiddleware. Superuser/staff bypass is handled
    in the app layer (`unscoped_context()` / `is_unscoped_allowed()`), not via RLS.
    """

    def process_request(self, request):
        if not getattr(settings, 'ENABLE_RLS', True):
            return None
        org = getattr(request, 'org', None)
        if org is not None and get_current_org() != org:
            logger.warning('RLS: tenant context differed from request.org; realigned')
            set_current_org(org)
        return None
