"""WorkerCycle: pass ordering and the invitation-sweep throttle. Exercised
with fakes for all three collaborators (QueueWorker, EmailDispatchService,
InterviewInvitationRepository) -- WorkerCycle only ever calls one method on
each, so no database is needed to prove the orchestration itself is correct.
"""
from datetime import datetime, timedelta, timezone

from application.worker_cycle import WorkerCycle


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeQueueWorker:
    def __init__(self, *, process_result=None, prewarm_result=None) -> None:
        self.process_result = process_result
        self.prewarm_result = prewarm_result
        self.process_calls = 0
        self.prewarm_calls = 0

    def process_next(self):
        self.process_calls += 1
        return self.process_result

    def prewarm_next(self):
        self.prewarm_calls += 1
        return self.prewarm_result


class FakeEmailDispatch:
    def __init__(self, *, result=None) -> None:
        self.result = result
        self.calls = 0

    def process_next(self):
        self.calls += 1
        return self.result


class FakeInvitationRepo:
    def __init__(self, *, expired=None) -> None:
        self.expired = expired if expired is not None else []
        self.calls = 0

    def expire_stale(self, *, now):
        self.calls += 1
        return self.expired


def test_a_real_interview_attempt_takes_priority_over_everything_else() -> None:
    queue_worker = FakeQueueWorker(process_result="an-item")
    email_dispatch = FakeEmailDispatch(result="an-email")
    invitation_repo = FakeInvitationRepo(expired=["an-invitation"])
    cycle = WorkerCycle(queue_worker, email_dispatch, invitation_repo, clock=FrozenClock(datetime.now(timezone.utc)))

    did_work = cycle.run_once()

    assert did_work is True
    assert queue_worker.process_calls == 1
    assert queue_worker.prewarm_calls == 0  # never reached
    assert email_dispatch.calls == 0
    assert invitation_repo.calls == 0


def test_prewarming_runs_when_no_real_attempt_is_due() -> None:
    queue_worker = FakeQueueWorker(process_result=None, prewarm_result="prewarmed")
    email_dispatch = FakeEmailDispatch(result="an-email")
    invitation_repo = FakeInvitationRepo()
    cycle = WorkerCycle(queue_worker, email_dispatch, invitation_repo, clock=FrozenClock(datetime.now(timezone.utc)))

    did_work = cycle.run_once()

    assert did_work is True
    assert queue_worker.prewarm_calls == 1
    assert email_dispatch.calls == 0  # never reached


def test_email_drains_when_queue_and_prewarm_have_nothing_to_do() -> None:
    queue_worker = FakeQueueWorker()
    email_dispatch = FakeEmailDispatch(result="an-email")
    invitation_repo = FakeInvitationRepo()
    cycle = WorkerCycle(queue_worker, email_dispatch, invitation_repo, clock=FrozenClock(datetime.now(timezone.utc)))

    did_work = cycle.run_once()

    assert did_work is True
    assert email_dispatch.calls == 1
    assert invitation_repo.calls == 0  # never reached


def test_invitation_sweep_runs_only_when_every_other_pass_is_idle() -> None:
    queue_worker = FakeQueueWorker()
    email_dispatch = FakeEmailDispatch()
    invitation_repo = FakeInvitationRepo(expired=["an-invitation"])
    cycle = WorkerCycle(queue_worker, email_dispatch, invitation_repo, clock=FrozenClock(datetime.now(timezone.utc)))

    did_work = cycle.run_once()

    assert did_work is True
    assert invitation_repo.calls == 1


def test_a_fully_idle_cycle_reports_no_work() -> None:
    cycle = WorkerCycle(
        FakeQueueWorker(), FakeEmailDispatch(), FakeInvitationRepo(),
        clock=FrozenClock(datetime.now(timezone.utc)),
    )
    assert cycle.run_once() is False


def test_invitation_sweep_does_not_repeat_within_its_interval() -> None:
    clock = FrozenClock(datetime.now(timezone.utc))
    invitation_repo = FakeInvitationRepo(expired=["an-invitation"])
    cycle = WorkerCycle(
        FakeQueueWorker(), FakeEmailDispatch(), invitation_repo,
        invitation_sweep_interval_seconds=300, clock=clock,
    )

    first = cycle.run_once()
    assert first is True
    assert invitation_repo.calls == 1

    # Still within the interval: the sweep must not run again, even though
    # every other pass is still idle.
    second = cycle.run_once()
    assert second is False
    assert invitation_repo.calls == 1


def test_invitation_sweep_runs_again_once_its_interval_elapses() -> None:
    clock = FrozenClock(datetime.now(timezone.utc))
    invitation_repo = FakeInvitationRepo(expired=["an-invitation"])
    cycle = WorkerCycle(
        FakeQueueWorker(), FakeEmailDispatch(), invitation_repo,
        invitation_sweep_interval_seconds=300, clock=clock,
    )
    cycle.run_once()
    assert invitation_repo.calls == 1

    clock.advance(timedelta(seconds=301))
    again = cycle.run_once()

    assert again is True
    assert invitation_repo.calls == 2


def test_run_forever_sleeps_only_when_a_full_cycle_finds_no_work() -> None:
    queue_worker = FakeQueueWorker(process_result=None)
    cycle = WorkerCycle(
        queue_worker, FakeEmailDispatch(), FakeInvitationRepo(),
        clock=FrozenClock(datetime.now(timezone.utc)),
    )
    sleeps: list[float] = []
    passes = {"count": 0}

    def should_continue() -> bool:
        passes["count"] += 1
        return passes["count"] <= 2

    cycle.run_forever(sleeps.append, should_continue=should_continue)

    assert sleeps, "an idle cycle must back off rather than spin"
