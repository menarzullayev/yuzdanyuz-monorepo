"""ISSUE-505 — Feature flag helpers.

Thin wrapper around `django-flags` to give services a consistent API:

    from core.feature_flags import is_enabled

    if is_enabled('ENABLE_REAL_PAYMENTS', request=request):
        ... real charge ...
    elif is_enabled('EXPERIMENTAL_NEW_DASHBOARD', user=user):
        ... new UI ...

`request=` form gives the flag library access to user + session for
per-user/per-percentage conditions. `user=` form is for background tasks
(no HTTP request available — only user-level conditions evaluate).

Flag definitions live in `settings.FLAGS`. Admin UI: `/admin/flags/`.
"""

from typing import Any

from flags.state import flag_enabled as _flag_enabled


def is_enabled(flag_name: str, *, request=None, **kwargs: Any) -> bool:
    """Return True if flag is currently enabled for the given context.

    Args:
        flag_name: Name from settings.FLAGS (uppercase convention).
        request: HttpRequest — preferred (gives access to user + session).
        **kwargs: Extra conditions (e.g. `user=...` for non-HTTP contexts).

    Returns:
        bool. Unknown flag → False (safe default, fail-closed).
    """
    try:
        return bool(_flag_enabled(flag_name, request=request, **kwargs))
    except Exception:
        # Unknown flag / misconfigured condition → safe default
        return False


def feature_on(name: str, *, request=None, **kwargs: Any) -> bool:
    """Env switch AND runtime kill switch (audit H05).

    ``settings.<name>`` (from the environment) decides whether a feature may run at all;
    the django-flags flag of the same name, on by default, lets staff turn it OFF at
    runtime from /admin/flags/ (add a *required* boolean ``False`` condition) without a
    config change or restart. A flag can never switch on what the environment left off:
    real payments without provider keys, AI parsing without a backend, SMS without a
    gateway.
    """
    from django.conf import settings

    if not getattr(settings, name, False):
        return False
    return is_enabled(name, request=request, **kwargs)
