"""Onboarding gate for student RESULT endpoints.

Required onboarding fields are asked before the first result, not at signup: starting and taking
exams stays open, seeing a score does not. Platform staff are never gated.
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.accounts.services.onboarding import missing_fields


class OnboardingRequired(PermissionDenied):
    default_code = 'onboarding_required'
    default_detail = 'Complete your profile (onboarding) before viewing results.'

    def __init__(self, missing: list[str]):
        super().__init__(detail={'detail': str(self.default_detail), 'missing': missing})


def _exempt(user) -> bool:
    return bool(user.is_staff or user.is_superuser)


def missing_for(user) -> list[str]:
    return [] if _exempt(user) else missing_fields(user)


def require_onboarding(user) -> None:
    """Raise ``OnboardingRequired`` (403, code ``onboarding_required``) for an incomplete profile."""
    missing = missing_for(user)
    if missing:
        raise OnboardingRequired(missing)


class ResultsOnboardingGate(BasePermission):
    """Use after IsAuthenticated: ``permission_classes = [IsAuthenticated, ResultsOnboardingGate]``."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False  # IsAuthenticated answers first; never reveal onboarding state to anons
        require_onboarding(user)
        return True
