"""DRF permission other apps use to gate the first RESULT behind onboarding."""

from rest_framework.permissions import BasePermission

from apps.accounts.services.onboarding import is_complete


class OnboardingComplete(BasePermission):
    """Allow only users who answered the required onboarding questions.

    Usage (e.g. in the exams app, on result endpoints)::

        permission_classes = [IsAuthenticated, OnboardingComplete]
    """

    code = 'onboarding_required'
    message = 'Complete your profile (onboarding) before viewing results.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and is_complete(user))
