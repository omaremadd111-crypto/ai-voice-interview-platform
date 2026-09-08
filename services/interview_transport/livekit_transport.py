"""LiveKit room transport for a prepared interview session.

This adapter dispatches a named voice agent and then observes the same persisted
session that every other transport uses. It never sees candidate audio or text,
and it never advances interview state itself.
"""
import json
import time
from collections.abc import Callable

from models.common import InterviewState
from services.interview_transport.base import (
    InterviewSubmitter,
    InterviewTransport,
    InterviewTransportContext,
    TransportOutcome,
    TransportResult,
)
from services.livekit.base import (
    LiveKitConfigurationError,
    LiveKitDispatch,
    LiveKitGatewayError,
    LiveKitRoomPort,
    room_name_for_session,
)


class LiveKitInterviewTransport(InterviewTransport):
    def __init__(
        self,
        gateway: LiveKitRoomPort,
        *,
        agent_name: str,
        join_timeout_seconds: float,
        interview_timeout_seconds: float,
        poll_interval_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._gateway = gateway
        self._agent_name = agent_name
        self._join_timeout = join_timeout_seconds
        self._interview_timeout = interview_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep

    @property
    def name(self) -> str:
        return "livekit"

    def run(
        self,
        session_id: str,
        submitter: InterviewSubmitter,
        *,
        context: InterviewTransportContext | None = None,
    ) -> TransportResult:
        if context is None:
            return TransportResult(
                outcome=TransportOutcome.FAILED,
                session_id=session_id,
                detail="LiveKit transport requires queue attempt context.",
            )

        metadata = json.dumps(
            {
                "session_id": session_id,
                "queue_item_id": context.queue_item_id,
                "candidate_id": context.candidate_id,
                "position_id": context.position_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            self._gateway.dispatch(
                LiveKitDispatch(
                    room_name=room_name_for_session(session_id),
                    agent_name=self._agent_name,
                    metadata=metadata,
                )
            )
        except (LiveKitConfigurationError, LiveKitGatewayError) as exc:
            return TransportResult(
                outcome=TransportOutcome.FAILED,
                session_id=session_id,
                detail=str(exc),
            )

        started_at = self._monotonic()
        interview_started_at: float | None = None
        while True:
            context.heartbeat()
            status = submitter.get_status(session_id)
            now = self._monotonic()
            state = InterviewState(status.state)
            if state in {InterviewState.COMPLETED, InterviewState.EVALUATED}:
                return TransportResult(
                    outcome=TransportOutcome.COMPLETED,
                    session_id=session_id,
                    answers_submitted=status.total_turns,
                )
            # The voice agent starts the core only after the expected candidate
            # participant joins. Pauses, rephrasing, and redirection deliberately
            # do not add transcript turns, so total_turns is not a valid join signal.
            if state is InterviewState.IN_PROGRESS and interview_started_at is None:
                interview_started_at = now
            if interview_started_at is None and now - started_at >= self._join_timeout:
                self._close_room_safely(session_id)
                return TransportResult(
                    outcome=TransportOutcome.NO_ANSWER,
                    session_id=session_id,
                    detail="No candidate joined the LiveKit room before the join timeout.",
                )
            if (
                interview_started_at is not None
                and now - interview_started_at >= self._interview_timeout
            ):
                if state is InterviewState.IN_PROGRESS:
                    submitter.end_interview(session_id)
                self._close_room_safely(session_id)
                return TransportResult(
                    outcome=TransportOutcome.FAILED,
                    session_id=session_id,
                    answers_submitted=status.total_turns,
                    detail="The LiveKit interview exceeded its configured duration.",
                )
            self._sleep(self._poll_interval)

    def _close_room_safely(self, session_id: str) -> None:
        try:
            self._gateway.close_room(room_name_for_session(session_id))
        except (LiveKitConfigurationError, LiveKitGatewayError):
            # The attempt outcome remains authoritative. Cleanup errors must not
            # turn a clear no-answer/timeout into a different queue result.
            return
