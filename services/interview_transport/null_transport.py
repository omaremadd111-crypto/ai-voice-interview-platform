"""Null / Mock interview transport -- a simulated conversation channel.

There is no candidate on the other end. This transport exists so the queue worker
can be built, run, and proven end to end (queue -> candidate -> approved plan ->
completed screening -> next candidate) before any real voice channel exists, and
so the worker keeps a channel it can run under test forever after.

Two rules keep it honest:

  * Nothing it produces is ever presented as a real candidate's words. Every
    generated answer carries SIMULATED_ANSWER_MARKER, and the marker is what tests
    and reviewers grep for.
  * It is fully deterministic -- the same script and the same plan always produce
    the same transcript. No randomness, no clock reads, nothing that could make a
    downstream score vary between runs.
"""
from collections.abc import Callable, Mapping

from models.common import InterviewState, QuestionCategory
from models.interview import NextPrompt
from services.interview_transport.base import (
    InterviewSubmitter,
    InterviewTransport,
    InterviewTransportContext,
    TransportOutcome,
    TransportResult,
)

#: Prefix stamped on every simulated answer. Mock Mode must never be mistaken for
#: a real conversation (docs/ARCHITECTURE.md: label simulated output explicitly).
SIMULATED_ANSWER_MARKER = "[SIMULATED — Demo / Mock Mode, no real candidate]"

#: Hard stop so a misbehaving engine can never spin this loop forever. Generous
#: relative to any real plan: questions plus their follow-ups.
_MAX_TURNS = 200

#: Signature of a scripted answer source: (question_text, category, turn_index).
AnswerScript = Callable[[str, QuestionCategory | None, int], str]


def scripted_answers(answers_by_category: Mapping[str, str]) -> AnswerScript:
    """Build an answer script from a category -> answer mapping.

    Used by the end-to-end demo with sample_data/answers_*.json, so a simulated run
    exercises the real evaluation path with substantive text rather than filler.
    Any category the mapping does not cover falls back to the default answer.
    """

    def script(question_text: str, category: QuestionCategory | None, turn_index: int) -> str:
        if category is not None:
            answer = answers_by_category.get(category.value)
            if answer:
                return f"{SIMULATED_ANSWER_MARKER} {answer}"
        return default_answer(question_text, category, turn_index)

    return script


def default_answer(question_text: str, category: QuestionCategory | None, turn_index: int) -> str:
    """The fallback simulated answer: clearly labelled and obviously not a person.

    It intentionally carries no substance. An interview built from these answers
    should evaluate to "insufficient evidence" -- which is the correct outcome for
    a conversation that never happened.
    """
    label = category.value if category is not None else "unknown"
    return (
        f"{SIMULATED_ANSWER_MARKER} No candidate was contacted. Placeholder response "
        f"for turn {turn_index} in category '{label}'."
    )


class NullInterviewTransport(InterviewTransport):
    """Drives a prepared session to completion with simulated answers.

    Note what this class does NOT do: it never inspects the plan, never decides
    when the interview ends, and never counts follow-ups. It asks the submitter
    for the next prompt and answers it until the engine says ``finished`` -- so
    follow-up limits and state transitions stay exclusively the engine's.
    """

    def __init__(
        self,
        answer_script: AnswerScript | None = None,
        *,
        fail_with: TransportOutcome | None = None,
        fail_detail: str | None = None,
    ) -> None:
        """``fail_with`` makes the transport report NO_ANSWER or FAILED without
        conducting a conversation, so retry/backoff behaviour can be exercised
        without pretending a real call failed."""
        self._answer_script = answer_script or default_answer
        if fail_with is TransportOutcome.COMPLETED:
            raise ValueError("fail_with must be a failure outcome, not COMPLETED")
        self._fail_with = fail_with
        self._fail_detail = fail_detail

    @property
    def name(self) -> str:
        return "null"

    def run(
        self,
        session_id: str,
        submitter: InterviewSubmitter,
        *,
        context: InterviewTransportContext | None = None,
    ) -> TransportResult:
        if self._fail_with is not None:
            return TransportResult(
                outcome=self._fail_with,
                session_id=session_id,
                detail=self._fail_detail or f"Simulated {self._fail_with.value} from the null transport.",
            )

        prompt: NextPrompt = submitter.start_interview(session_id)
        submitted = 0
        while not prompt.finished and submitted < _MAX_TURNS:
            question_text = prompt.question_text or ""
            answer = self._answer_script(question_text, prompt.category, submitted)
            prompt = submitter.submit_answer(session_id, answer)
            submitted += 1

        if not prompt.finished:
            # Close the session rather than abandoning it mid-interview, so the
            # partial transcript is on file instead of stuck IN_PROGRESS.
            self._close_if_open(session_id, prompt, submitter)
            return TransportResult(
                outcome=TransportOutcome.FAILED,
                session_id=session_id,
                answers_submitted=submitted,
                detail=f"Interview did not finish within {_MAX_TURNS} turns.",
            )

        self._close_if_open(session_id, prompt, submitter)
        return TransportResult(
            outcome=TransportOutcome.COMPLETED,
            session_id=session_id,
            answers_submitted=submitted,
        )

    @staticmethod
    def _close_if_open(
        session_id: str, prompt: NextPrompt, submitter: InterviewSubmitter,
    ) -> None:
        """End the interview only if the engine has not already ended it.

        Answering the last planned question completes the session inside the
        engine; end_interview() is the early-hangup path and is invalid from
        COMPLETED. Checking rather than assuming keeps the state machine the
        engine's alone -- the transport never drives a transition it does not own.
        """
        if prompt.state is not InterviewState.COMPLETED:
            submitter.end_interview(session_id)
