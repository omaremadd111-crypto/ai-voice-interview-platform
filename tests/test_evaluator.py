"""Evaluator agent tests: per-category evaluation, question-category mapping,
and evidence traceability verification (the fabricated-evidence downgrade path).
"""
import logging

import pytest

from agents.evaluator import Evaluator, _is_traceable, _questions_for_evaluation_category
from models.common import EvaluationCategory, QuestionCategory
from models.evaluation import CategoryEvaluation
from models.interview import InterviewPlan, InterviewQuestion, InterviewTranscript, InterviewTurn
from services.llm.base import LLMRequest, LLMService
from services.llm.mock.mock_service import MockLLMService

STRONG_ANSWER = (
    "I built a Python API using SQL and Docker because performance mattered under heavy "
    "load, so I optimized the query layer and reduced average latency by 40 percent, which "
    "allowed the team to scale confidently across every new service we shipped that quarter."
)


def _question(qid: str, category: QuestionCategory, expected_topics: list[str] | None = None) -> InterviewQuestion:
    return InterviewQuestion(
        id=qid, category=category, question=f"Question for {qid}?", purpose="test",
        expected_topics=expected_topics or ["Python"], difficulty="medium", follow_up_allowed=True,
    )


def _turn(qid: str, category: QuestionCategory, answer: str, is_follow_up: bool = False) -> InterviewTurn:
    return InterviewTurn(
        question_id=qid, question=f"Question for {qid}?", category=category,
        answer=answer, is_follow_up=is_follow_up, timestamp="2026-08-19T00:00:00Z",
    )


@pytest.fixture
def evaluator() -> Evaluator:
    return Evaluator(MockLLMService())


class _FabricatingLLMService(LLMService):
    """Always returns a CategoryEvaluation with evidence that was never actually said."""

    def __init__(self) -> None:
        self.provider_name = "fabricator"
        self.is_mock = True

    def generate_structured(self, request: LLMRequest, schema):
        return CategoryEvaluation(
            category=EvaluationCategory.TECHNICAL_KNOWLEDGE, score=95, sufficient_evidence=True,
            reasoning="Looks great.",
            evidence=["This exact sentence was never actually said by the candidate."],
            areas_to_validate=[],
        )

    def generate_text(self, request: LLMRequest) -> str:
        return "n/a"


class _PartiallyTraceableLLMService(LLMService):
    def __init__(self) -> None:
        self.provider_name = "partial"
        self.is_mock = True

    def generate_structured(self, request: LLMRequest, schema):
        return CategoryEvaluation(
            category=EvaluationCategory.TECHNICAL_KNOWLEDGE,
            score=82,
            sufficient_evidence=True,
            reasoning="One exact excerpt is sufficient; one item is invalid.",
            evidence=[
                "I built a Python API using SQL and Docker",
                "This fabricated sentence was never said.",
            ],
            areas_to_validate=[],
        )

    def generate_text(self, request: LLMRequest) -> str:
        return "n/a"


# ---- question <-> evaluation category mapping ----

def test_introduction_and_closing_never_map_to_any_evaluation_category() -> None:
    plan = InterviewPlan(questions=[
        _question("intro", QuestionCategory.INTRODUCTION),
        _question("closing", QuestionCategory.CLOSING),
    ])
    for category in EvaluationCategory:
        assert _questions_for_evaluation_category(plan, category) == []


def test_technical_question_maps_to_technical_knowledge() -> None:
    plan = InterviewPlan(questions=[_question("q1", QuestionCategory.TECHNICAL)])
    matched = _questions_for_evaluation_category(plan, EvaluationCategory.TECHNICAL_KNOWLEDGE)
    assert [q.id for q in matched] == ["q1"]


@pytest.mark.parametrize(
    ("source_category", "evaluation_categories"),
    [
        (
            QuestionCategory.CV_PROJECT_VALIDATION,
            {
                EvaluationCategory.RELEVANT_EXPERIENCE,
                EvaluationCategory.TECHNICAL_KNOWLEDGE,
                EvaluationCategory.JOB_REQUIREMENT_COVERAGE,
            },
        ),
        (
            QuestionCategory.BEHAVIORAL,
            {
                EvaluationCategory.COMMUNICATION,
                EvaluationCategory.BEHAVIORAL_COMPETENCIES,
            },
        ),
        (
            QuestionCategory.TECHNICAL,
            {
                EvaluationCategory.TECHNICAL_KNOWLEDGE,
                EvaluationCategory.JOB_REQUIREMENT_COVERAGE,
            },
        ),
    ],
)
def test_cross_category_evidence_sources_are_mapped(
    source_category: QuestionCategory,
    evaluation_categories: set[EvaluationCategory],
) -> None:
    plan = InterviewPlan(questions=[_question("q1", source_category)])
    matched = {
        category
        for category in EvaluationCategory
        if _questions_for_evaluation_category(plan, category)
    }
    assert evaluation_categories <= matched


def test_cv_validation_evidence_supports_experience_and_technical_knowledge(
    evaluator: Evaluator,
) -> None:
    plan = InterviewPlan(questions=[
        _question("cv", QuestionCategory.CV_PROJECT_VALIDATION, ["Python", "SQL"]),
    ])
    transcript = InterviewTranscript(turns=[
        _turn("cv", QuestionCategory.CV_PROJECT_VALIDATION, STRONG_ANSWER),
    ])
    for category in (
        EvaluationCategory.RELEVANT_EXPERIENCE,
        EvaluationCategory.TECHNICAL_KNOWLEDGE,
    ):
        result = evaluator.evaluate_category(category, plan, transcript)
        assert result.sufficient_evidence is True
        assert result.evidence


def test_behavioral_evidence_supports_communication_and_behavioral_competencies(
    evaluator: Evaluator,
) -> None:
    answer = (
        "I explained the delivery risk to stakeholders, facilitated a team discussion, "
        "documented the decision, and followed up after the release succeeded."
    )
    plan = InterviewPlan(questions=[
        _question("behavior", QuestionCategory.BEHAVIORAL, ["Communication"]),
    ])
    transcript = InterviewTranscript(turns=[
        _turn("behavior", QuestionCategory.BEHAVIORAL, answer),
    ])
    for category in (
        EvaluationCategory.COMMUNICATION,
        EvaluationCategory.BEHAVIORAL_COMPETENCIES,
    ):
        result = evaluator.evaluate_category(category, plan, transcript)
        assert result.sufficient_evidence is True
        assert result.evidence


def test_technical_evidence_supports_job_requirement_coverage(evaluator: Evaluator) -> None:
    plan = InterviewPlan(questions=[
        _question("technical", QuestionCategory.TECHNICAL, ["Python", "SQL"]),
    ])
    transcript = InterviewTranscript(turns=[
        _turn("technical", QuestionCategory.TECHNICAL, STRONG_ANSWER),
    ])
    result = evaluator.evaluate_category(
        EvaluationCategory.JOB_REQUIREMENT_COVERAGE,
        plan,
        transcript,
    )
    assert result.sufficient_evidence is True
    assert result.evidence


# ---- evaluate_category / evaluate_all_categories ----

def test_evaluate_category_returns_valid_category_evaluation(evaluator: Evaluator) -> None:
    plan = InterviewPlan(questions=[_question("q1", QuestionCategory.TECHNICAL, expected_topics=["Python"])])
    transcript = InterviewTranscript(turns=[_turn("q1", QuestionCategory.TECHNICAL, STRONG_ANSWER)])
    result = evaluator.evaluate_category(EvaluationCategory.TECHNICAL_KNOWLEDGE, plan, transcript)
    assert isinstance(result, CategoryEvaluation)
    assert result.category == EvaluationCategory.TECHNICAL_KNOWLEDGE
    assert result.sufficient_evidence is True
    assert result.score is not None


def test_evaluate_category_insufficient_evidence_with_no_relevant_turns(evaluator: Evaluator) -> None:
    plan = InterviewPlan(questions=[_question("q1", QuestionCategory.TECHNICAL)])
    transcript = InterviewTranscript(turns=[])  # nothing answered
    result = evaluator.evaluate_category(EvaluationCategory.TECHNICAL_KNOWLEDGE, plan, transcript)
    assert result.score is None
    assert result.sufficient_evidence is False


def test_evaluate_all_categories_returns_one_per_category(evaluator: Evaluator) -> None:
    plan = InterviewPlan(questions=[_question("q1", QuestionCategory.TECHNICAL)])
    transcript = InterviewTranscript(turns=[_turn("q1", QuestionCategory.TECHNICAL, STRONG_ANSWER)])
    results = evaluator.evaluate_all_categories(plan, transcript)
    assert {r.category for r in results} == set(EvaluationCategory)
    assert len(results) == len(EvaluationCategory)


def test_evaluate_all_categories_respects_explicit_category_subset(evaluator: Evaluator) -> None:
    plan = InterviewPlan(questions=[_question("q1", QuestionCategory.TECHNICAL)])
    transcript = InterviewTranscript(turns=[])
    results = evaluator.evaluate_all_categories(
        plan, transcript, categories=[EvaluationCategory.TECHNICAL_KNOWLEDGE, EvaluationCategory.COMMUNICATION],
    )
    assert {r.category for r in results} == {EvaluationCategory.TECHNICAL_KNOWLEDGE, EvaluationCategory.COMMUNICATION}


def test_evaluator_deterministic_with_mock_llm_service() -> None:
    plan = InterviewPlan(questions=[_question("q1", QuestionCategory.TECHNICAL, expected_topics=["Python"])])
    transcript = InterviewTranscript(turns=[_turn("q1", QuestionCategory.TECHNICAL, STRONG_ANSWER)])
    first = Evaluator(MockLLMService()).evaluate_category(EvaluationCategory.TECHNICAL_KNOWLEDGE, plan, transcript)
    second = Evaluator(MockLLMService()).evaluate_category(EvaluationCategory.TECHNICAL_KNOWLEDGE, plan, transcript)
    assert first == second


# ---- evidence traceability: mock never needs downgrading ----

def test_is_traceable_matches_verbatim_and_ellipsis_trimmed_evidence() -> None:
    turns = [_turn("q1", QuestionCategory.TECHNICAL, "I built a caching layer for the retrieval pipeline.")]
    assert _is_traceable("I built a caching layer", turns) is True
    assert _is_traceable("I built a caching layer…", turns) is True
    assert _is_traceable("This was never said at all", turns) is False


def test_is_traceable_accepts_only_formatting_normalization_not_paraphrase() -> None:
    turns = [_turn("q1", QuestionCategory.TECHNICAL, "I used “Python” — with SQL.")]
    assert _is_traceable('i used "python" - with sql', turns) is True
    assert _is_traceable("I developed a Python and SQL service", turns) is False


def test_mock_evidence_always_passes_traceability_verification(evaluator: Evaluator) -> None:
    plan = InterviewPlan(questions=[_question("q1", QuestionCategory.TECHNICAL, expected_topics=["Python", "SQL"])])
    transcript = InterviewTranscript(turns=[_turn("q1", QuestionCategory.TECHNICAL, STRONG_ANSWER)])
    result = evaluator.evaluate_category(EvaluationCategory.TECHNICAL_KNOWLEDGE, plan, transcript)
    if result.sufficient_evidence:
        for item in result.evidence:
            stripped = item[:-1] if item.endswith("…") else item
            assert stripped in STRONG_ANSWER


# ---- evidence traceability: fabricated evidence gets downgraded ----

def test_fabricated_evidence_is_downgraded_to_insufficient() -> None:
    evaluator = Evaluator(_FabricatingLLMService())
    plan = InterviewPlan(questions=[_question("q1", QuestionCategory.TECHNICAL)])
    transcript = InterviewTranscript(turns=[_turn("q1", QuestionCategory.TECHNICAL, "A real, different answer.")])

    result = evaluator.evaluate_category(EvaluationCategory.TECHNICAL_KNOWLEDGE, plan, transcript)

    assert result.score is None
    assert result.sufficient_evidence is False
    assert result.evidence == []
    assert result.reasoning == "Insufficient evidence"


def test_untraceable_items_are_dropped_when_other_exact_evidence_remains() -> None:
    evaluator = Evaluator(_PartiallyTraceableLLMService())
    plan = InterviewPlan(questions=[_question("q1", QuestionCategory.TECHNICAL)])
    transcript = InterviewTranscript(turns=[
        _turn("q1", QuestionCategory.TECHNICAL, STRONG_ANSWER),
    ])

    result = evaluator.evaluate_category(
        EvaluationCategory.TECHNICAL_KNOWLEDGE,
        plan,
        transcript,
    )

    assert result.score == 82
    assert result.sufficient_evidence is True
    assert result.evidence == ["I built a Python API using SQL and Docker"]


def test_fabricated_evidence_downgrade_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("interview_agent")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    records: list[str] = []
    handler.emit = lambda record: records.append(record.getMessage())  # type: ignore[method-assign]
    logger.addHandler(handler)
    try:
        evaluator = Evaluator(_FabricatingLLMService())
        plan = InterviewPlan(questions=[_question("q1", QuestionCategory.TECHNICAL)])
        transcript = InterviewTranscript(turns=[_turn("q1", QuestionCategory.TECHNICAL, "A real, different answer.")])
        evaluator.evaluate_category(EvaluationCategory.TECHNICAL_KNOWLEDGE, plan, transcript, session_id="sess-1")
    finally:
        logger.removeHandler(handler)
    assert any("EVIDENCE_DOWNGRADED" in r for r in records)
    assert any("sess-1" in r for r in records)
