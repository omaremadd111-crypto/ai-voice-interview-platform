"""Interviewer agent tests: proposes follow-ups only, via task="follow_up"."""
import pytest

from agents.interviewer import Interviewer
from models.common import QuestionCategory
from models.interview import FollowUpDecision, InterviewQuestion, InterviewTurn
from services.llm.mock.mock_service import MockLLMService

WEAK_ANSWER = "It was fine."
STRONG_ANSWER = (
    "I built a Python API using SQL and Docker because performance mattered under heavy "
    "load, so I optimized the query layer and reduced average latency by 40 percent, which "
    "allowed the team to scale confidently across every new service we shipped that quarter."
)


@pytest.fixture
def interviewer() -> Interviewer:
    return Interviewer(MockLLMService())


def _question(expected_topics=None, follow_up_allowed: bool = True) -> InterviewQuestion:
    return InterviewQuestion(
        id="q1", category=QuestionCategory.TECHNICAL, question="Tell me about your Python experience.",
        purpose="assess technical depth", expected_topics=expected_topics or ["Python"],
        difficulty="medium", follow_up_allowed=follow_up_allowed,
    )


def test_returns_follow_up_decision(interviewer: Interviewer) -> None:
    decision = interviewer.propose_follow_up(_question(), [], WEAK_ANSWER)
    assert isinstance(decision, FollowUpDecision)


def test_proposes_follow_up_for_weak_answer(interviewer: Interviewer) -> None:
    decision = interviewer.propose_follow_up(_question(), [], WEAK_ANSWER)
    assert decision.should_ask is True
    assert decision.follow_up_question


def test_declines_follow_up_for_strong_answer(interviewer: Interviewer) -> None:
    decision = interviewer.propose_follow_up(_question(), [], STRONG_ANSWER)
    assert decision.should_ask is False


def test_follow_up_is_only_proposed_for_a_material_evidence_gap(
    interviewer: Interviewer,
) -> None:
    question = _question(expected_topics=["Python", "SQL"])

    missing_evidence = interviewer.propose_follow_up(
        question,
        [],
        "I built the service in Python and reduced its latency by 40 percent.",
    )
    sufficient_evidence = interviewer.propose_follow_up(
        question,
        [],
        (
            "I built the service in Python with SQL, optimized the query layer, and reduced "
            "latency by 40 percent under production load."
        ),
    )

    assert missing_evidence.should_ask is True
    assert "SQL" in (missing_evidence.follow_up_question or "")
    assert sufficient_evidence.should_ask is False


def test_declines_when_question_disallows_follow_up(interviewer: Interviewer) -> None:
    decision = interviewer.propose_follow_up(_question(follow_up_allowed=False), [], WEAK_ANSWER)
    assert decision.should_ask is False


def test_uses_prior_turns_for_this_question_as_context(interviewer: Interviewer) -> None:
    # A weak-looking latest fragment should not be flagged as missing the topic if an
    # earlier turn for this same question already covered it.
    prior_turn = InterviewTurn(
        question_id="q1", question="Tell me about your Python experience.",
        category=QuestionCategory.TECHNICAL, answer=STRONG_ANSWER,
        is_follow_up=False, timestamp="2026-08-19T00:00:00Z",
    )
    decision = interviewer.propose_follow_up(_question(), [prior_turn], "Yes, that's right.")
    assert decision.should_ask is False


def test_does_not_expose_state_management_fields() -> None:
    # The agent's only output is a FollowUpDecision -- no counters, budgets, scores,
    # or feedback fields exist on it for the agent to (mis)manage.
    fields = FollowUpDecision.model_fields
    assert set(fields) == {"should_ask", "follow_up_question", "reason"}
