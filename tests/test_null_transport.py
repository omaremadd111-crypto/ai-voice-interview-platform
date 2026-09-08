"""NullInterviewTransport: the simulated conversation channel used by the worker.

Two properties matter more than the mechanics: the transport never decides
anything about the interview (it only answers what the engine asks, until the
engine says it is finished), and everything it produces is unmistakably labelled
as simulated.
"""
import pytest

from models.common import InterviewState, QuestionCategory
from models.interview import NextPrompt
from services.interview_transport import (
    SIMULATED_ANSWER_MARKER,
    NullInterviewTransport,
    TransportOutcome,
    scripted_answers,
)


class FakeSubmitter:
    """Stands in for InterviewAgentService, exposing only the InterviewSubmitter port."""

    def __init__(self, question_count: int) -> None:
        self._question_count = question_count
        self.answers: list[str] = []
        self.started = 0
        self.ended = 0

    def _prompt(self, index: int) -> NextPrompt:
        if index >= self._question_count:
            # Mirrors InterviewEngine: answering the last planned question
            # completes the session, so `finished` and COMPLETED arrive together.
            return NextPrompt(state=InterviewState.COMPLETED, finished=True, progress_pct=100.0)
        return NextPrompt(
            state=InterviewState.IN_PROGRESS,
            question_id=f"q{index + 1}",
            question_text=f"Question {index + 1}?",
            category=QuestionCategory.TECHNICAL,
            question_index=index,
            total_questions=self._question_count,
        )

    def start_interview(self, session_id: str) -> NextPrompt:
        self.started += 1
        return self._prompt(0)

    def submit_answer(self, session_id: str, answer: str) -> NextPrompt:
        self.answers.append(answer)
        return self._prompt(len(self.answers))

    def end_interview(self, session_id: str) -> object:
        self.ended += 1
        return object()


def test_runs_every_question_the_engine_offers() -> None:
    submitter = FakeSubmitter(question_count=4)
    result = NullInterviewTransport().run("session-1", submitter)

    assert result.outcome is TransportOutcome.COMPLETED
    assert result.answers_submitted == 4
    assert submitter.started == 1


def test_transport_does_not_re_end_an_interview_the_engine_already_ended() -> None:
    """The engine completes the session on the last answer. Calling end_interview()
    on top of that would be an invalid transition -- the transport must not try to
    drive a state change it does not own."""
    submitter = FakeSubmitter(question_count=2)
    NullInterviewTransport().run("session-1", submitter)
    assert submitter.ended == 0


def test_every_simulated_answer_is_labelled_as_simulated() -> None:
    submitter = FakeSubmitter(question_count=3)
    NullInterviewTransport().run("session-1", submitter)

    assert submitter.answers, "the transport submitted nothing"
    for answer in submitter.answers:
        assert answer.startswith(SIMULATED_ANSWER_MARKER)


def test_transport_stops_when_the_engine_says_finished_not_on_its_own_count() -> None:
    """The engine owns the end of the interview. A transport that decided for
    itself would be a second interview implementation."""
    submitter = FakeSubmitter(question_count=0)
    result = NullInterviewTransport().run("session-1", submitter)

    assert result.answers_submitted == 0
    assert submitter.answers == []
    assert result.outcome is TransportOutcome.COMPLETED


def test_answers_are_deterministic_across_runs() -> None:
    first, second = FakeSubmitter(question_count=5), FakeSubmitter(question_count=5)
    NullInterviewTransport().run("session-1", first)
    NullInterviewTransport().run("session-2", second)
    assert first.answers == second.answers


def test_scripted_answers_use_the_category_script_and_stay_labelled() -> None:
    script = scripted_answers({"technical": "I built a retrieval pipeline in Python."})
    submitter = FakeSubmitter(question_count=2)
    NullInterviewTransport(script).run("session-1", submitter)

    for answer in submitter.answers:
        assert answer.startswith(SIMULATED_ANSWER_MARKER)
        assert "retrieval pipeline" in answer


def test_scripted_answers_fall_back_when_a_category_is_missing() -> None:
    script = scripted_answers({"behavioral": "A time I disagreed with a teammate."})
    submitter = FakeSubmitter(question_count=1)
    NullInterviewTransport(script).run("session-1", submitter)

    # The fake asks TECHNICAL questions, which the script does not cover.
    assert submitter.answers[0].startswith(SIMULATED_ANSWER_MARKER)
    assert "No candidate was contacted" in submitter.answers[0]


@pytest.mark.parametrize("outcome", [TransportOutcome.NO_ANSWER, TransportOutcome.FAILED])
def test_configured_failure_reports_without_conducting_a_conversation(
    outcome: TransportOutcome,
) -> None:
    submitter = FakeSubmitter(question_count=3)
    result = NullInterviewTransport(fail_with=outcome).run("session-1", submitter)

    assert result.outcome is outcome
    assert submitter.started == 0
    assert submitter.answers == []


def test_completed_is_not_a_valid_simulated_failure() -> None:
    with pytest.raises(ValueError):
        NullInterviewTransport(fail_with=TransportOutcome.COMPLETED)


def test_a_never_finishing_engine_fails_instead_of_looping_forever() -> None:
    class NeverFinishes(FakeSubmitter):
        def submit_answer(self, session_id: str, answer: str) -> NextPrompt:
            self.answers.append(answer)
            return self._prompt(0)

    submitter = NeverFinishes(question_count=1)
    result = NullInterviewTransport().run("session-1", submitter)

    assert result.outcome is TransportOutcome.FAILED
    # Still IN_PROGRESS, so the transport closes it: a partial transcript on file
    # beats a session stuck open forever.
    assert submitter.ended == 1
    assert result.detail is not None and "did not finish" in result.detail
