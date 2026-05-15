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
