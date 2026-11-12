"""The rate-limit window is fixed: requests inside it must not extend it.

Found by the 2026-11-12 load test (docs/operations/load-test-results.md): check()
refreshed the counter's EXPIRE on every request, so for anyone sending at least
one request a minute the counter never reset. A student answering a 90-question
mock hit the 60/min API tier at answer 61 (and, through the penalty ladder, got
blocked for up to 2 hours) at an actual rate of ~25 requests a minute.
"""

import pytest

from core.utils import rate_limiter as rl


@pytest.fixture(autouse=True)
def isolate_rate_limiter(monkeypatch, mock_redis):
    monkeypatch.setattr(rl, '_redis', lambda: mock_redis)
    yield


@pytest.mark.unit
def test_request_inside_window_does_not_extend_it(mock_redis):
    ident = 'user:window-test'
    rl.check(ident, rl.TIER_API)
    key = f'rl:count:{rl.TIER_API.name}:{ident}'
    mock_redis.expire(key, 2)  # the window is about to end
    rl.check(ident, rl.TIER_API)
    assert 0 < mock_redis.ttl(key) <= 2


@pytest.mark.unit
def test_steady_user_is_never_limited(mock_redis):
    """150 requests at 30/min (the window rolls over every 60s) never raise."""
    ident = 'user:steady'
    key = f'rl:count:{rl.TIER_API.name}:{ident}'
    for i in range(150):
        if i and i % 30 == 0:
            mock_redis.delete(key)  # what Redis does when the 60s window expires
        rl.check(ident, rl.TIER_API)
    assert mock_redis.ttl(f'rl:block:{ident}') < 0


@pytest.mark.unit
def test_burst_over_limit_is_still_blocked():
    ident = 'user:burst'
    for _ in range(rl.TIER_API.limit):
        rl.check(ident, rl.TIER_API)
    with pytest.raises(rl.RateLimitExceeded):
        rl.check(ident, rl.TIER_API)


@pytest.mark.unit
def test_org_aggregate_window_is_fixed_too(mock_redis):
    rl.check_with_org_aggregate('user:a', 'org:x', rl.TIER_BROWSE, org_limit=1000)
    key = f'rl:count:{rl.TIER_BROWSE.name}_org:org:x'
    mock_redis.expire(key, 2)
    rl.check_with_org_aggregate('user:b', 'org:x', rl.TIER_BROWSE, org_limit=1000)
    assert 0 < mock_redis.ttl(key) <= 2
