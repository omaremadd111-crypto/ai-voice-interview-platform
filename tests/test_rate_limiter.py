"""ApplicationRateLimiter: pure arithmetic against an injected clock and a
faked repository -- no database needed, mirroring
tests/test_voice_invite_service.py's SimpleNamespace-fake style for a
similarly-shaped service.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from application.rate_limiter import ApplicationRateLimiter, RateLimitExceededError, RateLimitSettings

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _limiter(*, ip_count: int = 0, position_count: int = 0, per_ip_per_hour: int = 5) -> ApplicationRateLimiter:
    repo = SimpleNamespace(
        count_recent_for_ip=lambda ip_hash, since: ip_count,
        count_recent_for_position=lambda position_id, since: position_count,
    )
    return ApplicationRateLimiter(repo, RateLimitSettings(per_ip_per_hour=per_ip_per_hour), clock=lambda: NOW)


def test_settings_reject_a_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        RateLimitSettings(per_ip_per_hour=0)


def test_allows_a_first_application_from_a_fresh_ip() -> None:
    _limiter(ip_count=0).check(position_id=1, ip_hash="hash-a", max_applications_per_day=None)


def test_blocks_once_the_per_ip_limit_is_reached() -> None:
    limiter = _limiter(ip_count=5, per_ip_per_hour=5)
    with pytest.raises(RateLimitExceededError):
        limiter.check(position_id=1, ip_hash="hash-a", max_applications_per_day=None)


def test_allows_one_below_the_per_ip_limit() -> None:
    limiter = _limiter(ip_count=4, per_ip_per_hour=5)
    limiter.check(position_id=1, ip_hash="hash-a", max_applications_per_day=None)


def test_a_null_ip_hash_skips_the_per_ip_check_entirely() -> None:
    # Never fabricate a fake IP to check against -- absent means untracked.
    limiter = _limiter(ip_count=999, per_ip_per_hour=1)
    limiter.check(position_id=1, ip_hash=None, max_applications_per_day=None)


def test_a_null_max_per_day_means_no_position_level_limit() -> None:
    limiter = _limiter(position_count=999)
    limiter.check(position_id=1, ip_hash=None, max_applications_per_day=None)


def test_blocks_once_the_position_daily_cap_is_reached() -> None:
    limiter = _limiter(position_count=10)
    with pytest.raises(RateLimitExceededError):
        limiter.check(position_id=1, ip_hash=None, max_applications_per_day=10)


def test_allows_one_below_the_position_daily_cap() -> None:
    limiter = _limiter(position_count=9)
    limiter.check(position_id=1, ip_hash=None, max_applications_per_day=10)


def test_the_error_message_never_reveals_the_actual_count() -> None:
    limiter = _limiter(ip_count=5, per_ip_per_hour=5)
    with pytest.raises(RateLimitExceededError) as excinfo:
        limiter.check(position_id=1, ip_hash="hash-a", max_applications_per_day=None)
    assert "5" not in str(excinfo.value)
