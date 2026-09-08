from dataclasses import dataclass

from models.common import InterviewState
from services.interview_transport.base import InterviewTransportContext, TransportOutcome
from services.interview_transport.livekit_transport import LiveKitInterviewTransport
from services.livekit.base import LiveKitDispatch


class FakeGateway:
    def __init__(self) -> None:
        self.dispatches: list[LiveKitDispatch] = []
        self.closed_rooms: list[str] = []

    def dispatch(self, dispatch: LiveKitDispatch) -> None:
        self.dispatches.append(dispatch)

    def close_room(self, room_name: str) -> None:
        self.closed_rooms.append(room_name)


@dataclass
class Status:
    state: InterviewState
    total_turns: int


class FakeSubmitter:
    def __init__(self, statuses: list[Status]) -> None:
        self.statuses = statuses
        self.index = 0
        self.ended = False

    def get_status(self, session_id: str) -> Status:
        status = self.statuses[min(self.index, len(self.statuses) - 1)]
        self.index += 1
        return status

    def end_interview(self, session_id: str) -> object:
        self.ended = True
        return object()


def test_livekit_transport_dispatches_ids_and_observes_core_completion() -> None:
    gateway = FakeGateway()
    submitter = FakeSubmitter(
        [Status(InterviewState.READY, 0), Status(InterviewState.COMPLETED, 2)]
    )
    heartbeats: list[bool] = []
    ticks = iter([0.0, 0.1, 0.2])
    transport = LiveKitInterviewTransport(
        gateway,
        agent_name="hr-screening-agent",
        join_timeout_seconds=10,
        interview_timeout_seconds=60,
        poll_interval_seconds=0,
        monotonic=lambda: next(ticks),
        sleep=lambda _: None,
    )

    result = transport.run(
        "session-123",
        submitter,  # type: ignore[arg-type]
        context=InterviewTransportContext(7, 8, 9, lambda: heartbeats.append(True)),
    )

    assert result.outcome is TransportOutcome.COMPLETED
    assert result.answers_submitted == 2
    assert len(heartbeats) == 2
    assert gateway.dispatches[0].room_name == "hr-screen-session-123"
    assert '"candidate_id":8' in gateway.dispatches[0].metadata
    assert "answer" not in gateway.dispatches[0].metadata


def test_livekit_transport_returns_no_answer_after_join_timeout() -> None:
    gateway = FakeGateway()
    submitter = FakeSubmitter([Status(InterviewState.READY, 0)])
    ticks = iter([0.0, 5.0])
    transport = LiveKitInterviewTransport(
        gateway,
        agent_name="agent",
        join_timeout_seconds=5,
        interview_timeout_seconds=60,
        poll_interval_seconds=0,
        monotonic=lambda: next(ticks),
        sleep=lambda _: None,
    )

    result = transport.run(
        "session",
        submitter,  # type: ignore[arg-type]
        context=InterviewTransportContext(1, 2, 3, lambda: None),
    )

    assert result.outcome is TransportOutcome.NO_ANSWER
    assert "timeout" in (result.detail or "")
    assert gateway.closed_rooms == ["hr-screen-session"]


def test_joined_candidate_can_pause_before_submitting_first_answer() -> None:
    gateway = FakeGateway()
    submitter = FakeSubmitter(
        [
            Status(InterviewState.READY, 0),
            Status(InterviewState.IN_PROGRESS, 0),
            Status(InterviewState.COMPLETED, 1),
        ]
    )
    ticks = iter([0.0, 4.0, 6.0, 7.0])
    transport = LiveKitInterviewTransport(
        gateway,
        agent_name="agent",
        join_timeout_seconds=5,
        interview_timeout_seconds=60,
        poll_interval_seconds=0,
        monotonic=lambda: next(ticks),
        sleep=lambda _: None,
    )

    result = transport.run(
        "session",
        submitter,  # type: ignore[arg-type]
        context=InterviewTransportContext(1, 2, 3, lambda: None),
    )

    assert result.outcome is TransportOutcome.COMPLETED
    assert gateway.closed_rooms == []


def test_livekit_transport_requires_queue_context() -> None:
    result = LiveKitInterviewTransport(
        FakeGateway(),
        agent_name="agent",
        join_timeout_seconds=5,
        interview_timeout_seconds=60,
    ).run("session", FakeSubmitter([Status(InterviewState.READY, 0)]))  # type: ignore[arg-type]

    assert result.outcome is TransportOutcome.FAILED
