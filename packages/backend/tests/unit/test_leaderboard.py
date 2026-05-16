"""
Unit tests for `apps.engagement.leaderboard` module-level helpers.

Scope: focused on pure helpers + thin Redis wrappers (key builders, record,
top, rank, total, score_of, reset). Integration scenarios live in
`tests/integration/test_leaderboard.py`.

Uses `mock_redis` (fakeredis) from conftest.
"""

from unittest.mock import patch

import pytest

from apps.engagement import leaderboard


@pytest.mark.unit
class TestKeyBuilders:
    def test_key_mock(self):
        assert leaderboard.key_mock('abc') == 'lb:mock:abc'

    def test_key_global(self):
        assert leaderboard.key_global() == 'lb:global'

    def test_key_region(self):
        assert leaderboard.key_region(7) == 'lb:region:7'

    def test_key_tenant(self):
        assert leaderboard.key_tenant('org-uuid') == 'lb:tenant:org-uuid'

    def test_keys_are_distinct(self):
        keys = {
            leaderboard.key_mock('x'),
            leaderboard.key_global(),
            leaderboard.key_region('x'),
            leaderboard.key_tenant('x'),
        }
        assert len(keys) == 4


@pytest.mark.unit
class TestRecordAttempt:
    def test_writes_to_mock_and_global(self, db, mock_redis):
        leaderboard.record_attempt(user_id='u1', score=70.0, mock_id='m1')
        assert mock_redis.zscore('lb:mock:m1', 'u1') == 70.0
        assert mock_redis.zscore('lb:global', 'u1') == 70.0

    def test_writes_to_tenant_when_org_id_given(self, db, mock_redis):
        leaderboard.record_attempt(user_id='u1', score=42.0, mock_id='m', org_id='o1')
        assert mock_redis.zscore('lb:tenant:o1', 'u1') == 42.0

    def test_writes_to_region_when_region_id_given(self, db, mock_redis):
        leaderboard.record_attempt(user_id='u1', score=33.0, mock_id='m', region_id=5)
        assert mock_redis.zscore('lb:region:5', 'u1') == 33.0

    def test_global_keeps_max_score(self, db, mock_redis):
        leaderboard.record_attempt(user_id='u1', score=80.0, mock_id='m1')
        leaderboard.record_attempt(user_id='u1', score=50.0, mock_id='m2')
        # Mock-specific keeps actual score
        assert mock_redis.zscore('lb:mock:m1', 'u1') == 80.0
        assert mock_redis.zscore('lb:mock:m2', 'u1') == 50.0
        # Global keeps the max
        assert mock_redis.zscore('lb:global', 'u1') == 80.0

    def test_mock_overwrites_user_score(self, db, mock_redis):
        leaderboard.record_attempt(user_id='u1', score=80.0, mock_id='m1')
        leaderboard.record_attempt(user_id='u1', score=20.0, mock_id='m1')
        # Mock-specific: latest attempt wins (overwrite, not max)
        assert mock_redis.zscore('lb:mock:m1', 'u1') == 20.0

    def test_score_none_skipped(self, db, mock_redis):
        leaderboard.record_attempt(user_id='u1', score=None, mock_id='m1')
        assert mock_redis.zscore('lb:mock:m1', 'u1') is None

    def test_redis_error_swallowed(self, db):
        """Redis failures must not propagate (analytics layer fail-soft)."""
        with patch.object(leaderboard, '_redis') as mock_r:
            import redis as redis_pkg

            mock_r.return_value.zadd.side_effect = redis_pkg.RedisError('down')
            # Should not raise
            leaderboard.record_attempt(user_id='u1', score=50.0, mock_id='m')


@pytest.mark.unit
class TestZaddMaxFallback:
    def test_writes_when_new_member(self, db, mock_redis):
        leaderboard._zadd_max(mock_redis, 'k1', 'u1', 50.0)
        assert mock_redis.zscore('k1', 'u1') == 50.0

    def test_updates_when_score_greater(self, db, mock_redis):
        mock_redis.zadd('k1', {'u1': 30.0})
        leaderboard._zadd_max(mock_redis, 'k1', 'u1', 80.0)
        assert mock_redis.zscore('k1', 'u1') == 80.0

    def test_keeps_existing_when_score_lower(self, db, mock_redis):
        mock_redis.zadd('k1', {'u1': 90.0})
        leaderboard._zadd_max(mock_redis, 'k1', 'u1', 10.0)
        assert mock_redis.zscore('k1', 'u1') == 90.0


@pytest.mark.unit
class TestTop:
    def test_returns_descending_order(self, db, mock_redis):
        mock_redis.zadd('k', {'a': 10, 'b': 30, 'c': 20})
        result = leaderboard.top('k', limit=10)
        assert result == [('b', 30.0), ('c', 20.0), ('a', 10.0)]

    def test_respects_limit(self, db, mock_redis):
        mock_redis.zadd('k', {'a': 1, 'b': 2, 'c': 3, 'd': 4})
        result = leaderboard.top('k', limit=2)
        assert len(result) == 2
        assert result[0] == ('d', 4.0)

    def test_empty_key_returns_empty_list(self, db, mock_redis):
        assert leaderboard.top('nonexistent') == []

    def test_returns_tuples_with_floats(self, db, mock_redis):
        mock_redis.zadd('k', {'a': 7})
        result = leaderboard.top('k')
        assert isinstance(result[0][1], float)


@pytest.mark.unit
class TestRank:
    def test_zero_indexed_descending(self, db, mock_redis):
        mock_redis.zadd('k', {'a': 10, 'b': 30, 'c': 20})
        assert leaderboard.rank('k', 'b') == 0  # top
        assert leaderboard.rank('k', 'c') == 1
        assert leaderboard.rank('k', 'a') == 2

    def test_returns_none_when_user_missing(self, db, mock_redis):
        assert leaderboard.rank('k', 'ghost') is None

    def test_coerces_user_id_to_string(self, db, mock_redis):
        mock_redis.zadd('k', {'42': 5})
        # Pass int — service should str(...) it
        assert leaderboard.rank('k', 42) == 0


@pytest.mark.unit
class TestScoreOf:
    def test_returns_float(self, db, mock_redis):
        mock_redis.zadd('k', {'u': 99.5})
        s = leaderboard.score_of('k', 'u')
        assert s == 99.5
        assert isinstance(s, float)

    def test_returns_none_when_missing(self, db, mock_redis):
        assert leaderboard.score_of('k', 'ghost') is None


@pytest.mark.unit
class TestTotal:
    def test_counts_members(self, db, mock_redis):
        mock_redis.zadd('k', {'a': 1, 'b': 2, 'c': 3})
        assert leaderboard.total('k') == 3

    def test_zero_for_unknown_key(self, db, mock_redis):
        assert leaderboard.total('nonexistent') == 0


@pytest.mark.unit
class TestReset:
    def test_deletes_key(self, db, mock_redis):
        mock_redis.zadd('k', {'a': 1})
        assert mock_redis.exists('k') == 1
        leaderboard.reset('k')
        assert mock_redis.exists('k') == 0

    def test_reset_unknown_key_is_noop(self, db, mock_redis):
        # Should not raise
        leaderboard.reset('never-existed')
