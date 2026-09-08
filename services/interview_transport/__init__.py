"""Interview conversation channels.

``InterviewTransport`` keeps conversation channels outside the interview core.
The deterministic null/mock adapter remains the default; P6 adds a LiveKit room
adapter whose provider SDK is confined to ``services/livekit``.
"""
from services.interview_transport.base import (
    InterviewSubmitter,
    InterviewTransport,
    InterviewTransportContext,
    TransportOutcome,
    TransportResult,
)
from services.interview_transport.livekit_transport import LiveKitInterviewTransport
from services.interview_transport.null_transport import (
    SIMULATED_ANSWER_MARKER,
    AnswerScript,
    NullInterviewTransport,
    default_answer,
    scripted_answers,
)

__all__ = [
    "SIMULATED_ANSWER_MARKER",
    "AnswerScript",
    "InterviewSubmitter",
    "InterviewTransport",
    "InterviewTransportContext",
    "LiveKitInterviewTransport",
    "NullInterviewTransport",
    "TransportOutcome",
    "TransportResult",
    "default_answer",
    "scripted_answers",
]
