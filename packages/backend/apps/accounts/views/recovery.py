"""
Password recovery API (no email reset by design).

  POST /api/auth/recovery/telegram/   {username}            -> 202, bot sends a one-time link
  POST /api/auth/recovery/confirm/    {token, new_password}  -> 200
  POST /api/auth/recovery/request/    {username, contact}    -> 202, queued for platform admins
  POST /api/auth/recovery/org-reset/  {user_id, temporary_password?}  (org admin) -> temp password
  POST /api/auth/password/change-required/  see credentials.ForcedPasswordChangeView
"""

import uuid

from rest_framework.response import Response

from apps.accounts.models import CustomUser
from apps.accounts.services import recovery
from apps.accounts.services import usernames as un
from apps.accounts.views.base import AuthAPIView, PublicAPIView, api_error, field_errors
from apps.accounts.views.credentials import raw_text_field, text_field

ACCEPTED = {
    'ok': True,
    'message': 'Agar hisob mavjud va Telegram ulangan boʿlsa, havola yuborildi.',
}


class TelegramRecoveryView(PublicAPIView):
    """Always answers the same 202, so it does not reveal which usernames exist."""

    def post(self, request):
        name = un.normalize_username(text_field(request.data, 'username'))
        if name:
            allowed = recovery.allow_request(f'login:{name}')
            user = CustomUser.objects.filter(username__iexact=name).first()
            if user is not None and allowed:
                recovery.send_link_via_telegram(user)
        return Response(ACCEPTED, status=202)


class RecoveryConfirmView(PublicAPIView):
    def post(self, request):
        token = raw_text_field(request.data, 'token')
        new = raw_text_field(request.data, 'new_password')
        try:
            recovery.confirm_reset(token, new)
        except recovery.RecoveryError as e:
            if e.code == 'validation_error':
                return api_error(
                    'validation_error',
                    str(e),
                    400,
                    field_errors(new_password=e.errors),
                )
            return api_error(e.code, str(e), 400)
        return Response({'ok': True})


class AdminQueueRequestView(PublicAPIView):
    """Last resort: ask platform admins to reset the password (always 202)."""

    def post(self, request):
        name = un.normalize_username(text_field(request.data, 'username'))
        contact = text_field(request.data, 'contact')
        if name and recovery.allow_request(f'queue:{name}'):
            recovery.queue_request(name, contact)
        return Response({'ok': True}, status=202)


class OrgAdminResetView(AuthAPIView):
    """An org admin (``members:manage``) sets a temporary password for a member."""

    def post(self, request):
        try:
            target_id = uuid.UUID(str(request.data.get('user_id', '')))
        except ValueError:
            return api_error('validation_error', 'user_id is required.', 400)

        actor = request.user
        target = CustomUser.objects.filter(pk=target_id).first()
        if target is None or not self._may_reset(actor, target):
            # Same answer for "no such user" and "not yours": no probing of other orgs.
            return api_error('forbidden', 'Not allowed.', 403)

        supplied = raw_text_field(request.data, 'temporary_password') or None
        try:
            password = recovery.set_temporary_password(target, supplied)
        except recovery.RecoveryError as e:
            return api_error(
                'validation_error', str(e), 400, field_errors(temporary_password=e.errors)
            )
        return Response({'temporary_password': password, 'must_change_password': True})

    @staticmethod
    def _may_reset(actor: CustomUser, target: CustomUser) -> bool:
        if actor.pk == target.pk or target.is_staff or target.is_superuser:
            return False
        from apps.organizations.models import Membership, MembershipStatus

        org_ids = Membership.objects.filter(
            user=target, status=MembershipStatus.ACTIVE
        ).values_list('organization_id', flat=True)
        memberships = Membership.objects.filter(
            user=actor, organization_id__in=list(org_ids), status=MembershipStatus.ACTIVE
        ).select_related('role', 'organization')
        return any(m.has_permission('members:manage') for m in memberships)
