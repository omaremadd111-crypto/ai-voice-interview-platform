"""InterviewPlanner agent tests: question count, grounding, uniqueness, difficulty."""
from unittest.mock import patch

import pytest

from agents.interview_planner import MAX_QUESTIONS, MIN_QUESTIONS, InterviewPlanner
from models.candidate import CandidateAnalysis, FitAnalysis
from models.common import QuestionCategory
from models.interview import InterviewPlan, InterviewQuestion
from models.job import JobAnalysis
from services.llm.mock.mock_service import MockLLMService


@pytest.fixture
def planner() -> InterviewPlanner:
    return InterviewPlanner(MockLLMService())


@pytest.fixture
def job_analysis() -> JobAnalysis:
    return JobAnalysis(
        job_title="Junior AI Engineer",
        experience_level="Junior",
        required_skills=["Python", "SQL", "RAG"],
        nice_to_have_skills=["Cloud"],
        responsibilities=["Build and maintain retrieval-augmented generation systems."],
        technical_topics=["Python", "SQL", "RAG"],
        behavioral_competencies=["Communication"],
        role_summary="Junior AI Engineer role.",
    )


@pytest.fixture
def candidate_analysis() -> CandidateAnalysis:
    return CandidateAnalysis(
        skills=["Python", "RAG"],
        technologies=["Python", "RAG"],
        experience=["2 years as a software engineer."],
        projects=["Built a production RAG application."],
        education=["BSc Computer Science."],
        important_cv_claims=["Built a production RAG application."],
        relevant_experience=["2 years as a software engineer."],
        unclear_claims_to_validate=["Built a production RAG application."],
    )


@pytest.fixture
def fit_analysis() -> FitAnalysis:
    return FitAnalysis(
        strong_alignment_areas=["Python", "RAG"],
        relevant_candidate_experience=["2 years as a software engineer."],
        important_job_requirements=["Python", "SQL", "RAG"],
        skills_requiring_validation=["Built a production RAG application."],
        missing_information=["SQL"],
        questions_to_investigate=["Ask the candidate to elaborate on: Built a production RAG application."],
    )


def _plan(planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis,
          num_questions: int = 6, experience_level: str | None = "Junior") -> InterviewPlan:
    return planner.plan(
        "Junior AI Engineer", "FlairsTech", experience_level, num_questions,
        job_analysis, candidate_analysis, fit_analysis,
    )


# ---- question count ----

@pytest.mark.parametrize("requested", [3, 5, 6, 10, 15])
def test_returns_exactly_the_requested_number_of_main_questions(
    planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis, requested: int,
) -> None:
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, num_questions=requested)
    assert len(plan.questions) == requested


def test_rejects_num_questions_below_minimum(planner: InterviewPlanner, job_analysis,
                                              candidate_analysis, fit_analysis) -> None:
    with pytest.raises(ValueError):
        _plan(planner, job_analysis, candidate_analysis, fit_analysis, num_questions=MIN_QUESTIONS - 1)


def test_rejects_num_questions_above_maximum(planner: InterviewPlanner, job_analysis,
                                              candidate_analysis, fit_analysis) -> None:
    with pytest.raises(ValueError):
        _plan(planner, job_analysis, candidate_analysis, fit_analysis, num_questions=MAX_QUESTIONS + 1)


# ---- validity ----

def test_all_questions_are_valid_interview_question_objects(
    planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis,
) -> None:
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis)
    for q in plan.questions:
        assert isinstance(q, InterviewQuestion)
        assert q.id and q.question and q.purpose and q.difficulty
        assert isinstance(q.category, QuestionCategory)
        assert isinstance(q.expected_topics, list)
        assert isinstance(q.follow_up_allowed, bool)


def test_starts_with_introduction_and_ends_with_closing(
    planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis,
) -> None:
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis)
    assert plan.questions[0].category == QuestionCategory.INTRODUCTION
    assert plan.questions[-1].category == QuestionCategory.CLOSING


# ---- grounding: job requirements, CV claims, fit analysis ----

def test_uses_job_requirements(planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis) -> None:
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, num_questions=8)
    all_text = " ".join(q.question for q in plan.questions)
    assert any(skill in all_text for skill in job_analysis.required_skills)


def test_uses_cv_specific_claims(planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis) -> None:
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis)
    validation_questions = [q for q in plan.questions if q.category == QuestionCategory.CV_PROJECT_VALIDATION]
    assert len(validation_questions) >= 1
    assert any("Built a production RAG application" in q.question for q in validation_questions)


def test_uses_fit_analysis_missing_information(
    planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis,
) -> None:
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, num_questions=8)
    technical_questions = [q for q in plan.questions if q.category == QuestionCategory.TECHNICAL]
    # fit_analysis.missing_information = ["SQL"] should surface as a technical question.
    assert any("SQL" in q.question for q in technical_questions)


def test_technical_questions_never_reference_behavioral_competencies(
    planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis,
) -> None:
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, num_questions=10)
    technical_questions = [q for q in plan.questions if q.category == QuestionCategory.TECHNICAL]
    for q in technical_questions:
        for competency in job_analysis.behavioral_competencies:
            assert competency not in q.question


# ---- no duplicates, no generic/malformed template artifacts ----

def test_no_duplicate_question_text(planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis) -> None:
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, num_questions=15)
    texts = [q.question for q in plan.questions]
    assert len(texts) == len(set(texts))


def test_no_duplicate_question_ids(planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis) -> None:
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, num_questions=15)
    ids = [q.id for q in plan.questions]
    assert len(ids) == len(set(ids))


def test_no_double_wrapped_template_artifacts(
    planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis,
) -> None:
    # Regression test: a prior bug fed fit_analysis.questions_to_investigate (already a
    # full "Ask the candidate to elaborate on: ..." sentence) into another question
    # template, producing a nonsensical nested question.
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, num_questions=15)
    for q in plan.questions:
        assert "Ask the candidate to elaborate on" not in q.question
        assert q.question.count("Can you walk me through how you approached this?") <= 1


def test_does_not_ask_about_the_same_skill_repeatedly(
    planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis,
) -> None:
    plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, num_questions=10)
    technical_questions = [q for q in plan.questions if q.category == QuestionCategory.TECHNICAL]
    topics_asked = [t for q in technical_questions for t in q.expected_topics]
    assert len(topics_asked) == len(set(topics_asked))


# ---- difficulty adapts to experience level ----

def test_difficulty_adapts_to_experience_level(
    planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis,
) -> None:
    junior_plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, experience_level="Junior")
    senior_plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, experience_level="Senior")

    junior_technical = next(q for q in junior_plan.questions if q.category == QuestionCategory.TECHNICAL)
    senior_technical = next(q for q in senior_plan.questions if q.category == QuestionCategory.TECHNICAL)
    assert junior_technical.difficulty == "easy"
    assert senior_technical.difficulty == "hard"


def test_intro_and_closing_are_always_easy_regardless_of_level(
    planner: InterviewPlanner, job_analysis, candidate_analysis, fit_analysis,
) -> None:
    senior_plan = _plan(planner, job_analysis, candidate_analysis, fit_analysis, experience_level="Senior")
    assert senior_plan.questions[0].difficulty == "easy"
    assert senior_plan.questions[-1].difficulty == "easy"


# ---- candidate must never see expected_topics or scoring context ----

def test_expected_topics_are_internal_not_candidate_facing_language() -> None:
    # expected_topics is a data field on the model, not rendered into question text --
    # verified structurally: the model has no field exposing rubric/scoring to the candidate.
    fields = InterviewQuestion.model_fields
    assert "expected_topics" in fields
    assert "score" not in fields
    assert "rubric" not in fields


# ---- determinism ----

def test_deterministic_under_mock_llm_service(job_analysis, candidate_analysis, fit_analysis) -> None:
    first = InterviewPlanner(MockLLMService()).plan(
        "Junior AI Engineer", "FlairsTech", "Junior", 8, job_analysis, candidate_analysis, fit_analysis,
    )
    second = InterviewPlanner(MockLLMService()).plan(
        "Junior AI Engineer", "FlairsTech", "Junior", 8, job_analysis, candidate_analysis, fit_analysis,
    )
    assert first == second


def test_position_planning_request_contains_job_context_only(job_analysis: JobAnalysis) -> None:
    llm = MockLLMService()
    planner = InterviewPlanner(llm)

    with patch.object(llm, "generate_structured", wraps=llm.generate_structured) as generate:
        plan = planner.plan_position_questions(
            "Platform Engineer",
            "FlairsTech",
            "Senior",
            8,
            job_analysis,
        )

    request = generate.call_args.args[0]
    assert request.context["scope"] == "position_baseline"
    assert set(request.context) == {
        "scope",
        "job_title",
        "company_name",
        "experience_level",
        "num_questions",
        "job_analysis",
        "existing_questions",
    }
    assert "Candidate analysis:" not in request.user
    assert "Fit analysis:" not in request.user
    assert len(plan.questions) == 8
    assert {question.category for question in plan.questions} <= {
        QuestionCategory.CANDIDATE_BACKGROUND,
        QuestionCategory.TECHNICAL,
        QuestionCategory.PROBLEM_SOLVING,
        QuestionCategory.BEHAVIORAL,
    }
    rendered = " ".join(question.question for question in plan.questions).casefold()
    for forbidden in ("your cv", "your resume", "you mentioned", "i see you"):
        assert forbidden not in rendered


def test_position_planner_rejects_candidate_context_keyword(job_analysis: JobAnalysis) -> None:
    planner = InterviewPlanner(MockLLMService())

    with pytest.raises(TypeError):
        planner.plan_position_questions(
            "Platform Engineer",
            "FlairsTech",
            "Senior",
            6,
            job_analysis,
            candidate_analysis=CandidateAnalysis(full_name="Boundary Sentinel"),  # type: ignore[call-arg]
        )
