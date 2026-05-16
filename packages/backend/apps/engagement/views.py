"""
Task 5 — Leaderboard REST API.

Endpoints:
  GET /api/leaderboard/global/            top-N + me
  GET /api/leaderboard/region/             auto from request.user.region
  GET /api/leaderboard/tenant/             auto from request.org
  GET /api/leaderboard/mock/<mock_id>/    per-mock
  GET /api/leaderboard/me/?scope=...      faqat joriy user'ning rank/score

Query params:
  ?limit=<int>  top-N limit (default 100, max 500)

Auth: IsAuthenticated. Tenant context middleware orqali set qilingan.

Performance: barcha read operatsiyalar Redis O(log N) — minglab user uchun
bir-ikki millisekund.
"""

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import leaderboard
from .models import LeaderboardSnapshot
from .serializers import LeaderboardResponseSerializer

User = get_user_model()

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def _parse_limit(request) -> int:
    raw = request.query_params.get('limit', DEFAULT_LIMIT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({'limit': "limit raqam bo'lishi kerak."}) from None
    if n <= 0 or n > MAX_LIMIT:
        raise ValidationError({'limit': f"limit 1..{MAX_LIMIT} oralig'ida bo'lishi kerak."})
    return n


def _build_entries(top_results, *, scope_key, request):
    """
    Top results [(user_id_str, score), ...] → JSON entries with user info.
    Bitta query bilan barcha user'larni olib keladi (N+1 yo'q).
    """
    if not top_results:
        return []

    user_ids = [uid for uid, _ in top_results]  # UUID strings
    users_by_id = {
        str(u.pk): u
        for u in User.objects.filter(pk__in=user_ids)
        .select_related('region')
        .only('id', 'username', 'avatar', 'region__name_uz')
    }

    # Server-side rank (1-indexed for UI)
    entries = []
    for idx, (uid_str, score) in enumerate(top_results):
        u = users_by_id.get(uid_str)
        entries.append(
            {
                'rank': idx + 1,
                'user_pk': uid_str,
                'username': u.username if u else f'user_{uid_str[:8]}',
                'score': score,
                'region_name': (u.region.name_uz if u and u.region else None),
                'avatar': (u.avatar.url if u and u.avatar else None),
            }
        )
    return entries


def _me_payload(scope_key, user):
    """Joriy user'ning rank/score — frontend o'z holatini ko'rsatish uchun."""
    rank = leaderboard.rank(scope_key, user.pk)
    score = leaderboard.score_of(scope_key, user.pk)
    if rank is None:
        return {'in_leaderboard': False, 'rank': None, 'score': None}
    return {
        'in_leaderboard': True,
        'rank': rank + 1,  # 1-indexed for UI
        'score': score,
    }


# ─── Views ────────────────────────────────────────────────────────────────────


class GlobalLeaderboardView(APIView):
    """GET /api/leaderboard/global/"""

    def get(self, request):
        limit = _parse_limit(request)
        scope_key = leaderboard.key_global()
        entries = _build_entries(
            leaderboard.top(scope_key, limit), scope_key=scope_key, request=request
        )
        return Response(
            LeaderboardResponseSerializer(
                {
                    'scope': 'global',
                    'total': leaderboard.total(scope_key),
                    'entries': entries,
                    'me': _me_payload(scope_key, request.user),
                }
            ).data
        )


class RegionLeaderboardView(APIView):
    """GET /api/leaderboard/region/  — auto request.user.region orqali"""

    def get(self, request):
        if not request.user.region_id:
            return Response(
                {'detail': "Sizning profilingizda hudud ko'rsatilmagan."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        limit = _parse_limit(request)
        scope_key = leaderboard.key_region(request.user.region_id)
        entries = _build_entries(
            leaderboard.top(scope_key, limit), scope_key=scope_key, request=request
        )
        return Response(
            LeaderboardResponseSerializer(
                {
                    'scope': f'region:{request.user.region_id}',
                    'total': leaderboard.total(scope_key),
                    'entries': entries,
                    'me': _me_payload(scope_key, request.user),
                }
            ).data
        )


class TenantLeaderboardView(APIView):
    """GET /api/leaderboard/tenant/  — auto request.org orqali"""

    def get(self, request):
        org = getattr(request, 'org', None)
        if org is None:
            return Response(
                {'detail': 'Tenant context topilmadi.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        limit = _parse_limit(request)
        scope_key = leaderboard.key_tenant(org.id)
        entries = _build_entries(
            leaderboard.top(scope_key, limit), scope_key=scope_key, request=request
        )
        return Response(
            LeaderboardResponseSerializer(
                {
                    'scope': f'tenant:{org.id}',
                    'total': leaderboard.total(scope_key),
                    'entries': entries,
                    'me': _me_payload(scope_key, request.user),
                }
            ).data
        )


class MockLeaderboardView(APIView):
    """GET /api/leaderboard/mock/<mock_id>/  — per-mock"""

    def get(self, request, mock_id):
        limit = _parse_limit(request)
        scope_key = leaderboard.key_mock(mock_id)
        entries = _build_entries(
            leaderboard.top(scope_key, limit), scope_key=scope_key, request=request
        )
        return Response(
            LeaderboardResponseSerializer(
                {
                    'scope': f'mock:{mock_id}',
                    'total': leaderboard.total(scope_key),
                    'entries': entries,
                    'me': _me_payload(scope_key, request.user),
                }
            ).data
        )


class MyLeaderboardRankView(APIView):
    """
    GET /api/leaderboard/me/?scope=global|region|tenant|mock:<id>
    Yengil endpoint — top'siz, faqat user'ning rank+score.
    """

    def get(self, request):
        scope = request.query_params.get('scope', 'global')

        if scope == 'global':
            scope_key = leaderboard.key_global()
        elif scope == 'region':
            if not request.user.region_id:
                return Response(
                    {'detail': "Hudud ko'rsatilmagan."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            scope_key = leaderboard.key_region(request.user.region_id)
        elif scope == 'tenant':
            org = getattr(request, 'org', None)
            if org is None:
                return Response(
                    {'detail': 'Tenant context topilmadi.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            scope_key = leaderboard.key_tenant(org.id)
        elif scope.startswith('mock:'):
            mock_id = scope[len('mock:') :]
            scope_key = leaderboard.key_mock(mock_id)
        else:
            return Response(
                {'detail': f"Noma'lum scope: {scope}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(_me_payload(scope_key, request.user))


# ─── History (snapshot) ──────────────────────────────────────────────────────


class LeaderboardHistoryView(APIView):
    """
    GET /api/leaderboard/history/?period=weekly&scope_kind=global&period_key=2026-W19
                                                         &scope_id=<id>

    Tarixiy snapshot — Celery archive_leaderboards orqali yozilgan.

    Args (query):
      period: weekly | monthly | yearly  (required)
      scope_kind: global | region | tenant | mock  (required)
      scope_id: scope identifier (region_id / org_id / mock_id), global uchun bo'sh
      period_key: ixtiyoriy. Yo'q bo'lsa eng so'nggi snapshot qaytadi.
    """

    def get(self, request):
        period = request.query_params.get('period')
        scope_kind = request.query_params.get('scope_kind')
        scope_id = request.query_params.get('scope_id', '')
        period_key = request.query_params.get('period_key')

        if period not in ('weekly', 'monthly', 'yearly'):
            return Response(
                {'detail': 'period: weekly | monthly | yearly'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if scope_kind not in ('global', 'region', 'tenant', 'mock'):
            return Response(
                {'detail': 'scope_kind: global | region | tenant | mock'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = LeaderboardSnapshot.objects.filter(
            period=period, scope_kind=scope_kind, scope_id=scope_id
        )
        if period_key:
            qs = qs.filter(period_key=period_key)

        snapshot = qs.order_by('-taken_at').first()
        if snapshot is None:
            return Response({'detail': 'Snapshot topilmadi.'}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            {
                'period': snapshot.period,
                'period_key': snapshot.period_key,
                'scope_kind': snapshot.scope_kind,
                'scope_id': snapshot.scope_id,
                'taken_at': snapshot.taken_at,
                'total': snapshot.total,
                'entries': snapshot.entries,
            }
        )


# ─── Task 9 — Streak / Leagues / Notifications / Search ─────────────────────


from django.shortcuts import get_object_or_404  # noqa: E402
from django.utils import timezone  # noqa: E402

from . import leagues_service, search_service  # noqa: E402
from .models import LeagueMembership, Notification, UserStreak  # noqa: E402


class StreakView(APIView):
    """GET /api/engagement/streak/  — joriy user streak."""

    def get(self, request):
        try:
            s = request.user.streak
            return Response(
                {
                    'current_streak': s.current_streak,
                    'max_streak': s.max_streak,
                    'last_activity_date': s.last_activity_date,
                }
            )
        except UserStreak.DoesNotExist:
            return Response({'current_streak': 0, 'max_streak': 0, 'last_activity_date': None})


class CurrentLeagueView(APIView):
    """
    GET /api/engagement/leagues/current/
    Joriy hafta membership + liga ichidagi rank.
    """

    def get(self, request):
        m = leagues_service.get_or_create_membership(request.user)
        if m is None:
            return Response({'detail': "League catalog bo'sh."}, status=404)

        # Liga ichidagi rank
        rank = (
            LeagueMembership.objects.filter(
                league=m.league,
                period_start=m.period_start,
                points_earned__gt=m.points_earned,
            ).count()
            + 1
        )
        # Top 10 ko'rinish
        top = list(
            LeagueMembership.objects.filter(league=m.league, period_start=m.period_start)
            .order_by('-points_earned')
            .select_related('user')[:10]
        )

        return Response(
            {
                'league': {
                    'id': str(m.league.id),
                    'name': m.league.name,
                    'rank_order': m.league.rank_order,
                    'color_hex': m.league.color_hex,
                },
                'period_start': m.period_start,
                'period_end': m.period_end,
                'my_points': m.points_earned,
                'my_rank': rank,
                'top': [
                    {
                        'user_id': str(t.user_id),
                        'username': t.user.username,
                        'points': t.points_earned,
                    }
                    for t in top
                ],
            }
        )


class LeagueHistoryView(APIView):
    """GET /api/engagement/leagues/history/  — user'ning hafta tarixi."""

    def get(self, request):
        memberships = list(
            LeagueMembership.objects.filter(user=request.user)
            .select_related('league', 'next_league')
            .order_by('-period_start')[:20]
        )
        return Response(
            {
                'history': [
                    {
                        'period_start': m.period_start,
                        'period_end': m.period_end,
                        'league': m.league.name,
                        'points': m.points_earned,
                        'promoted': m.promoted,
                        'demoted': m.demoted,
                        'next_league': m.next_league.name if m.next_league else None,
                    }
                    for m in memberships
                ]
            }
        )


class NotificationListView(APIView):
    """GET /api/engagement/notifications/?unread=1"""

    def get(self, request):
        qs = Notification.objects.filter(user=request.user)
        if request.query_params.get('unread') == '1':
            qs = qs.exclude(status=Notification.Status.READ)
        items = list(qs.order_by('-created_at')[:50])
        return Response(
            {
                'count': len(items),
                'items': [
                    {
                        'id': str(n.id),
                        'channel': n.channel,
                        'priority': n.priority,
                        'status': n.status,
                        'title': n.title,
                        'body': n.body,
                        'metadata': n.metadata,
                        'created_at': n.created_at,
                        'read_at': n.read_at,
                    }
                    for n in items
                ],
            }
        )


class NotificationReadView(APIView):
    """POST /api/engagement/notifications/<id>/read/"""

    def post(self, request, notification_id):
        n = get_object_or_404(Notification, pk=notification_id, user=request.user)
        n.status = Notification.Status.READ
        n.read_at = timezone.now()
        n.save(update_fields=['status', 'read_at'])
        return Response({'detail': 'OK', 'status': n.status})


class SearchView(APIView):
    """GET /api/engagement/search/?q=..."""

    def get(self, request):
        q = request.query_params.get('q', '')
        if len(q.strip()) < 2:
            return Response({'detail': 'Eng kamida 2 ta belgi kiriting'}, status=400)
        try:
            limit = int(request.query_params.get('limit', 20))
        except (TypeError, ValueError):
            raise ValidationError({'limit': 'integer'}) from None
        if limit <= 0 or limit > 100:
            raise ValidationError({'limit': "1..100 oralig'ida"})

        org = getattr(request, 'org', None)
        results = search_service.search_questions(q, org=org, limit=limit)
        return Response({'count': len(results), 'results': results})
