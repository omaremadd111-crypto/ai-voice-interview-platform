"""The conversation-channel abstraction the queue worker calls.

A transport is responsible for ONE thing: getting a candidate's words into an
already-prepared interview session. It does not decide what to ask, how many
follow-ups are allowed, when the interview is over, or what any of it is worth --
all of that already lives in InterviewEngine behind InterviewAgentService, and a
transport reaches it only through the ``InterviewSubmitter`` port below.

That confinement is the whole point: when LiveKit arrives it becomes another
implementation of this interface, adapting audio to the same calls, and never a
second interview implementation.
"""
from abc import ABC, abstractmethod
from enum import StrEnum
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from models.interview import NextPrompt


class InterviewStatus(Protocol):
    """Read-only session status needed by a long-running room transport."""

    state: object
    total_turns: int


@dataclass(frozen=True)
class InterviewTransportContext:
    """Non-evaluative identifiers and a lease heartbeat for one queue attempt.

    No document text, candidate answer, rubric, or score is allowed here. The
    LiveKit agent resolves the candidate and persona from the database using
    these ids, while the queue worker retains ownership of lease renewal.
    """

    queue_item_id: int
    candidate_id: int
    position_id: int
    heartbeat: Callable[[], None]


class TransportOutcome(StrEnum):
    """How the conversation ended, from the channel's point of view.

    Describes the CALL, never the candidate. NO_ANSWER means nobody picked up or
    the channel could not reach them; it says nothing about their suitability and
    must never be read as one.
    """

    COMPLETED = "completed"
    NO_ANSWER = "no_answer"
    FAILED = "failed"


class TransportResult(BaseModel):
    outcome: TransportOutcome
    session_id: str
    #: Number of answers handed to the interview engine. Diagnostics only.
    answers_submitted: int = 0
    #: Channel-level failure detail (never candidate content) when not COMPLETED.
    detail: str | None = None


class InterviewSubmitter(Protocol):
    """The only slice of InterviewAgentService a transport may touch.

    Deliberately narrow: a transport can start the interview, push an answer, and
    close it. It cannot evaluate, score, or report -- so no channel can ever
    influence an assessment.
    """

    def start_interview(self, session_id: str) -> NextPrompt: ...

    def submit_answer(self, session_id: str, answer: str) -> NextPrompt: ...

    def end_interview(self, session_id: str) -> object: ...

    def get_status(self, session_id: str) -> InterviewStatus: ...


class InterviewTransport(ABC):
    """Runs one prepared interview session to its end over some channel."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier recorded in logs, e.g. "null"."""

    @abstractmethod
    def run(
        self,
        session_id: str,
        submitter: InterviewSubmitter,
        *,
        context: InterviewTransportContext | None = None,
    ) -> TransportResult:
        """Conduct the conversation for an already-prepared, approved session."""
