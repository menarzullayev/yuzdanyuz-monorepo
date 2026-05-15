"""
Task 5 — Leaderboard DRF serializers.

Frontend uchun: top entry har biri user info bilan (username, region, avatar)
va rank — server-side hisoblangan.
"""

from rest_framework import serializers


class LeaderboardEntrySerializer(serializers.Serializer):
    """Bitta leaderboard entry — front-end render uchun."""

    rank = serializers.IntegerField()
    user_id = serializers.UUIDField(source='user_pk')
    username = serializers.CharField()
    score = serializers.FloatField()
    region_name = serializers.CharField(allow_null=True, required=False)
    avatar = serializers.URLField(allow_null=True, required=False)


class LeaderboardResponseSerializer(serializers.Serializer):
    """Top-N + total + joriy user'ning rank/score."""

    scope = serializers.CharField()  # global / region / tenant / mock
    total = serializers.IntegerField()
    entries = LeaderboardEntrySerializer(many=True)
    me = serializers.DictField(required=False)
