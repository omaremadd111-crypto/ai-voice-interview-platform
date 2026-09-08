"""RetryPolicy: the backoff schedule the queue worker uses between attempts.

Kept deterministic on purpose. A test that has to tolerate jitter cannot assert a
schedule, and the whole project already forbids randomness in anything that
decides what happens to a candidate.
"""
import pytest

from application.queue_worker import RetryPolicy


def test_first_retry_waits_the_base_delay() -> None:
    policy = RetryPolicy(base_seconds=30, factor=2, max_seconds=900)
    assert policy.delay_for(1).total_seconds() == 30


def test_delay_doubles_with_each_spent_attempt() -> None:
    policy = RetryPolicy(base_seconds=30, factor=2, max_seconds=900)
    assert [policy.delay_for(n).total_seconds() for n in (1, 2, 3, 4)] == [30, 60, 120, 240]


def test_delay_is_capped_at_max_seconds() -> None:
    policy = RetryPolicy(base_seconds=30, factor=2, max_seconds=900)
    # 30 * 2**9 would be 15360s; the cap must hold no matter how high attempts go.
    assert policy.delay_for(10).total_seconds() == 900
    assert policy.delay_for(50).total_seconds() == 900


def test_delay_is_identical_across_calls_and_instances() -> None:
    first = RetryPolicy(base_seconds=45, factor=3, max_seconds=1000)
    second = RetryPolicy(base_seconds=45, factor=3, max_seconds=1000)
    assert first.delay_for(3) == second.delay_for(3) == first.delay_for(3)


def test_factor_of_one_gives_a_constant_delay() -> None:
    policy = RetryPolicy(base_seconds=60, factor=1, max_seconds=600)
    assert {policy.delay_for(n).total_seconds() for n in range(1, 6)} == {60}


def test_attempts_below_one_is_a_programming_error() -> None:
    policy = RetryPolicy()
    with pytest.raises(ValueError):
        policy.delay_for(0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_seconds": 0},
        {"base_seconds": -1},
        {"factor": 0},
        {"base_seconds": 100, "max_seconds": 10},
    ],
)
def test_invalid_policies_are_rejected_at_construction(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)
