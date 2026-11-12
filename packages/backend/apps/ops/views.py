"""Ops API: public status page, staff incident/probe/security/bot admin, audit journal."""

import logging
import re
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.views.base import AuthAPIView, api_error
from apps.ops.models import (
    BotSettings,
    ProbeResult,
    SecurityEvent,
    StatusIncident,
    StatusIncidentUpdate,
)
from apps.ops.permissions import (
    IsPlatformStaff,
    IsStaffOrOrgAdmin,
    admin_org_ids,
    is_platform_staff,
)
from apps.ops.services import audit, bot_config
from apps.ops.services.status import build_status, contains_ip_address, local_iso

log = logging.getLogger(__name__)


def _bad(msg: str, /, **fields):
    return api_error('validation_error', msg, 400, {k: [v] for k, v in fields.items()})


# ── Public status page ──────────────────────────────────────


class StatusView(APIView):
    """GET /api/v1/status/ — anonymous, read-only, no internal details."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        resp = Response(build_status())
        resp['Cache-Control'] = 'public, max-age=30'
        return resp


# ── Staff: incidents ────────────────────────────────────────


def _validate_text(value, field: str, max_len: int, required=True):
    if not isinstance(value, str) or (required and not value.strip()):
        return None, _bad(f'{field} is required.', **{field: 'required'})
    value = value.strip()
    if len(value) > max_len:
        return None, _bad(f'{field} is too long.', **{field: 'too_long'})
    if contains_ip_address(value):
        return None, _bad(
            'Public text must not contain IP addresses.', **{field: 'contains_ip_address'}
        )
    return value, None


def _dt(value, field: str):
    if value in (None, ''):
        return None, None
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None:
        return None, _bad(f'{field} must be an ISO datetime with offset.', **{field: 'invalid'})
    return parsed, None


def incident_row(inc: StatusIncident) -> dict:
    return {
        'id': str(inc.id),
        'kind': inc.kind,
        'title': inc.title,
        'impact': inc.impact,
        'status': inc.status,
        'started_at': local_iso(inc.started_at),
        'ends_at': local_iso(inc.ends_at),
        'resolved_at': local_iso(inc.resolved_at),
        'updates': [
            {'status': u.status, 'message': u.message, 'at': local_iso(u.created_at)}
            for u in inc.updates.all()
        ],
    }


class IncidentListCreateView(AuthAPIView):
    permission_classes = [IsPlatformStaff]

    def get(self, request):
        qs = StatusIncident.objects.prefetch_related('updates')[:100]
        return Response([incident_row(i) for i in qs])

    def post(self, request):
        d = request.data
        title, err = _validate_text(d.get('title'), 'title', 200)
        if err:
            return err
        message, err = _validate_text(d.get('message'), 'message', 2000)
        if err:
            return err
        kind = d.get('kind', StatusIncident.Kind.INCIDENT)
        impact = d.get('impact', StatusIncident.Impact.NONE)
        status = d.get('status', StatusIncident.Status.INVESTIGATING)
        if kind not in StatusIncident.Kind.values:
            return _bad('Invalid kind.', kind='invalid')
        if impact not in StatusIncident.Impact.values:
            return _bad('Invalid impact.', impact='invalid')
        if status not in StatusIncident.Status.values:
            return _bad('Invalid status.', status='invalid')
        started, err = _dt(d.get('started_at'), 'started_at')
        if err:
            return err
        ends, err = _dt(d.get('ends_at'), 'ends_at')
        if err:
            return err
        started = started or timezone.now()
        if ends is not None and ends <= started:
            return _bad('ends_at must be after started_at.', ends_at='before_start')
        with transaction.atomic():
            inc = StatusIncident.objects.create(
                kind=kind,
                title=title,
                impact=impact,
                status=status,
                started_at=started,
                ends_at=ends,
                resolved_at=timezone.now() if status == 'resolved' else None,
                created_by=request.user,
            )
            StatusIncidentUpdate.objects.create(
                incident=inc, status=status, message=message, created_by=request.user
            )
        return Response(incident_row(inc), status=201)


class IncidentDetailView(AuthAPIView):
    permission_classes = [IsPlatformStaff]

    def _get(self, pk):
        try:
            return StatusIncident.objects.prefetch_related('updates').filter(pk=pk).first()
        except (ValueError, TypeError):
            return None

    def patch(self, request, pk):
        inc = self._get(pk)
        if inc is None:
            return api_error('not_found', 'Incident not found.', 404)
        d = request.data
        if 'title' in d:
            title, err = _validate_text(d.get('title'), 'title', 200)
            if err:
                return err
            inc.title = title
        if 'impact' in d:
            if d['impact'] not in StatusIncident.Impact.values:
                return _bad('Invalid impact.', impact='invalid')
            inc.impact = d['impact']
        if 'ends_at' in d:
            ends, err = _dt(d['ends_at'], 'ends_at')
            if err:
                return err
            if ends is not None and ends <= inc.started_at:
                return _bad('ends_at must be after started_at.', ends_at='before_start')
            inc.ends_at = ends
        inc.save()
        return Response(incident_row(inc))


class IncidentUpdateCreateView(AuthAPIView):
    permission_classes = [IsPlatformStaff]

    def post(self, request, pk):
        inc = StatusIncident.objects.filter(pk=pk).first()
        if inc is None:
            return api_error('not_found', 'Incident not found.', 404)
        message, err = _validate_text(request.data.get('message'), 'message', 2000)
        if err:
            return err
        status = request.data.get('status')
        if status not in StatusIncident.Status.values:
            return _bad('Invalid status.', status='invalid')
        with transaction.atomic():
            StatusIncidentUpdate.objects.create(
                incident=inc, status=status, message=message, created_by=request.user
            )
            inc.status = status
            inc.resolved_at = (inc.resolved_at or timezone.now()) if status == 'resolved' else None
            inc.save()
        inc = StatusIncident.objects.prefetch_related('updates').get(pk=inc.pk)
        return Response(incident_row(inc), status=201)


class ProbeListView(AuthAPIView):
    permission_classes = [IsPlatformStaff]

    def get(self, request):
        rows = ProbeResult.objects.all()[:100]
        return Response(
            [
                {
                    'checked_at': local_iso(p.checked_at),
                    'ok': p.ok,
                    'db_ok': p.db_ok,
                    'db_ms': p.db_ms,
                    'redis_ok': p.redis_ok,
                    'redis_ms': p.redis_ms,
                    'ai_ok': p.ai_ok,
                    'ai_ms': p.ai_ms,
                }
                for p in rows
            ]
        )


# ── Staff: security dashboard ───────────────────────────────

RANGES = {'24h': timedelta(hours=24), '7d': timedelta(days=7)}


class SecurityDashboardView(AuthAPIView):
    permission_classes = [IsPlatformStaff]

    def get(self, request):
        rng = request.query_params.get('range', '24h')
        if rng not in RANGES:
            return _bad('range must be 24h or 7d.', range='invalid')
        since = timezone.now() - RANGES[rng]
        from django.db.models import Count, Max

        events = SecurityEvent.objects.filter(created_at__gte=since)
        by_type = dict.fromkeys(SecurityEvent.Type.values, 0)
        for row in events.values('event_type').annotate(n=Count('id')):
            by_type[row['event_type']] = row['n']
        top = (
            events.values('source')
            .annotate(n=Count('id'), last_seen=Max('created_at'))
            .order_by('-n', 'source')[:10]
        )
        latest = events.select_related('user')[:50]
        return Response(
            {
                'range': rng,
                'total': sum(by_type.values()),
                'by_type': by_type,
                'top_sources': [
                    {'source': r['source'], 'count': r['n'], 'last_seen': local_iso(r['last_seen'])}
                    for r in top
                ],
                'latest': [
                    {
                        'when': local_iso(e.created_at),
                        'type': e.event_type,
                        'source': e.source,
                        'path': e.path,
                        'reason': e.reason,
                        'user': e.user.username if e.user_id else None,
                    }
                    for e in latest
                ],
            }
        )


# ── Audit journal ───────────────────────────────────────────


class _AuditBase(AuthAPIView):
    permission_classes = [IsStaffOrOrgAdmin]

    def _scope(self, request):
        """Returns ((platform, org_id), None) or (None, error response)."""
        raw = request.query_params.get('org')
        org_id = None
        if raw:
            try:
                org_id = uuid.UUID(raw)
            except ValueError:
                return None, _bad('org must be a UUID.', org='invalid')
        if is_platform_staff(request.user):
            return (True, org_id), None
        mine = admin_org_ids(request.user)
        if org_id is None:
            if len(mine) != 1:
                return None, _bad('org is required.', org='required')
            org_id = mine[0]
        if org_id not in mine:
            return None, api_error('permission_denied', 'Not an admin of this organization.', 403)
        return (False, org_id), None

    def _filters(self, request):
        q = request.query_params
        model = q.get('model') or None
        if model and model not in audit.audited_models():
            return None, _bad('Unknown model.', model='invalid')
        action = q.get('action') or None
        if action and action not in audit.ACTION_TO_TYPE:
            return None, _bad('action must be created, changed or deleted.', action='invalid')
        user_id = q.get('user') or None
        if user_id:
            try:
                uuid.UUID(user_id)
            except ValueError:
                return None, _bad('user must be a user id (UUID).', user='invalid')
        dates = {}
        for key in ('date_from', 'date_to'):
            v = q.get(key) or None
            if v:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
                    return None, _bad(f'{key} must be YYYY-MM-DD.', **{key: 'invalid'})
                try:
                    audit._day_start(v)
                except ValueError:
                    return None, _bad(f'{key} must be YYYY-MM-DD.', **{key: 'invalid'})
            dates[key] = v
        return {'model': model, 'action': action, 'user_id': user_id, **dates}, None

    def _querysets(self, request):
        scope, err = self._scope(request)
        if err:
            return None, None, err
        filters, err = self._filters(request)
        if err:
            return None, None, err
        platform, org_id = scope
        qs = list(audit.scoped_querysets(org_id=org_id, platform=platform, **filters))
        return qs, scope, None


class AuditListView(_AuditBase):
    def get(self, request):
        querysets, scope, err = self._querysets(request)
        if err:
            return err
        try:
            page_no = max(1, int(request.query_params.get('page', 1)))
            size = min(100, max(1, int(request.query_params.get('page_size', 25))))
        except ValueError:
            return _bad('page and page_size must be integers.', page='invalid')
        rows, total = audit.page(querysets, (page_no - 1) * size, size)
        platform, org_id = scope
        return Response(
            {
                'count': total,
                'page': page_no,
                'page_size': size,
                'results': rows,
                'dangerous_today': audit.dangerous_today(org_id=org_id, platform=platform),
            }
        )


class AuditExportView(_AuditBase):
    def get(self, request):
        querysets, _, err = self._querysets(request)
        if err:
            return err
        rows, _ = audit.page(querysets, 0, audit.EXPORT_LIMIT)
        resp = HttpResponse(audit.to_csv(rows), content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = 'attachment; filename="audit-journal.csv"'
        return resp


# ── Staff: Telegram bot admin ───────────────────────────────

_TOKEN_RE = re.compile(r'^\d{5,}:[A-Za-z0-9_-]{30,}$')
_CHANNEL_RE = re.compile(r'^(@[A-Za-z][A-Za-z0-9_]{4,31}|-?\d{5,20})$')
CHANNEL_TEXT_MAX = 3500


def _https_url(value) -> bool:
    if not isinstance(value, str) or len(value) > 300:
        return False
    parts = urlsplit(value)
    return parts.scheme == 'https' and bool(parts.hostname) and not parts.username


def bot_payload() -> dict:
    row = BotSettings.objects.filter(pk=1).first()
    token, secret = bot_config.get_token(), bot_config.get_webhook_secret()
    return {
        'has_token': bool(token),
        'token_last4': bot_config.last4(token),
        'token_source': bot_config.source_of('token'),
        'webhook_url': row.webhook_url if row else '',
        'has_webhook_secret': bool(secret),
        'webhook_secret_last4': bot_config.last4(secret),
        'admin_chat_ids': bot_config.get_admin_chat_ids(),
        'channel_id': bot_config.get_channel_id(),
        'updated_at': local_iso(row.updated_at) if row else None,
    }


class BotSettingsView(AuthAPIView):
    permission_classes = [IsPlatformStaff]

    def get(self, request):
        return Response(bot_payload())

    def patch(self, request):
        from core import crypto

        d = request.data
        row = BotSettings.load()
        if 'token' in d:
            token = d['token']
            if not isinstance(token, str) or not _TOKEN_RE.match(token.strip()):
                return _bad('Bot token format is invalid.', token='invalid')
            row.token_enc = crypto.encrypt(token.strip())
        if 'webhook_url' in d:
            url = d['webhook_url']
            if url != '' and not _https_url(url):
                return _bad('webhook_url must be an https URL.', webhook_url='invalid')
            row.webhook_url = url
        if 'admin_chat_ids' in d:
            ids = d['admin_chat_ids']
            if (
                not isinstance(ids, list)
                or len(ids) > 20
                or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)
            ):
                return _bad(
                    'admin_chat_ids must be a list of up to 20 integers.', admin_chat_ids='invalid'
                )
            row.admin_chat_ids = ids
        if 'channel_id' in d:
            ch = d['channel_id']
            if ch != '' and not (isinstance(ch, str) and _CHANNEL_RE.match(ch)):
                return _bad(
                    'channel_id must be @username or a numeric chat id.', channel_id='invalid'
                )
            row.channel_id = ch
        if d.get('regenerate_secret') is True:
            row.webhook_secret_enc = crypto.encrypt(bot_config.generate_secret())
        row.updated_by = request.user
        row.save()
        bot_config.invalidate()
        return Response(bot_payload())


class BotSetWebhookView(AuthAPIView):
    permission_classes = [IsPlatformStaff]

    def post(self, request):
        from apps.accounts.services.bot_sender import call_api
        from core import crypto

        row = BotSettings.load()
        url = request.data.get('webhook_url', row.webhook_url)
        if not _https_url(url):
            return _bad('A https webhook_url is required.', webhook_url='required')
        if not bot_config.get_token():
            return api_error('bot_token_missing', 'Bot token is not configured.', 400)
        if not row.webhook_secret_enc and not bot_config.get_webhook_secret():
            row.webhook_secret_enc = crypto.encrypt(bot_config.generate_secret())
        row.webhook_url = url
        row.updated_by = request.user
        row.save()
        bot_config.invalidate()
        result = call_api(
            'setWebhook',
            {
                'url': url,
                'secret_token': bot_config.get_webhook_secret(),
                'allowed_updates': ['message', 'callback_query'],
            },
        )
        ok = bool(result.get('ok'))
        return Response(
            {'ok': ok, 'description': str(result.get('description', ''))[:200]},
            status=200 if ok else 502,
        )


class BotWebhookStatusView(AuthAPIView):
    permission_classes = [IsPlatformStaff]

    def get(self, request):
        from apps.accounts.services.bot_sender import call_api

        result = call_api('getWebhookInfo')
        if not result.get('ok'):
            return Response(
                {'ok': False, 'description': str(result.get('description', ''))[:200]}, status=502
            )
        info = result.get('result') or {}
        return Response(
            {
                'ok': True,
                'url': info.get('url', ''),
                'pending_update_count': info.get('pending_update_count', 0),
                'last_error_date': info.get('last_error_date'),
                'last_error_message': str(info.get('last_error_message', ''))[:200],
                'max_connections': info.get('max_connections'),
            }
        )


class BotPostChannelView(AuthAPIView):
    permission_classes = [IsPlatformStaff]

    def post(self, request):
        from apps.accounts.services.bot_sender import call_api

        d = request.data
        text = d.get('text')
        if not isinstance(text, str) or not text.strip():
            return _bad('text is required.', text='required')
        if len(text) > CHANNEL_TEXT_MAX:
            return _bad(f'text must be at most {CHANNEL_TEXT_MAX} characters.', text='too_long')
        chat_id = bot_config.get_channel_id()
        if not chat_id:
            return api_error('channel_not_configured', 'Channel is not configured.', 400)
        payload = {'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}
        button = d.get('button')
        if button is not None:
            if not (
                isinstance(button, dict)
                and isinstance(button.get('text'), str)
                and 0 < len(button['text']) <= 64
                and _https_url(button.get('url'))
            ):
                return _bad('button needs a text (<= 64) and an https url.', button='invalid')
            payload['reply_markup'] = {
                'inline_keyboard': [[{'text': button['text'], 'url': button['url']}]]
            }
        result = call_api('sendMessage', payload)
        if not result.get('ok'):
            return Response(
                {'ok': False, 'description': str(result.get('description', ''))[:200]}, status=502
            )
        log.info('Channel post sent by user=%s', request.user.pk)
        return Response({'ok': True, 'message_id': (result.get('result') or {}).get('message_id')})
