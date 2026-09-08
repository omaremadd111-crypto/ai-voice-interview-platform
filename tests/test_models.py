"""Schema validation for job/candidate/interview/evaluation/session models."""
import pytest
from pydantic import ValidationError

from models.common import (
    EvaluationCategory,
    ExperienceLevel,
    InterviewState,
    QuestionCategory,
    RecommendationLevel,
    ScreeningOutcome,
)
from models.candidate import CandidateAnalysis, CandidateInput, FitAnalysis
from models.evaluation import CategoryEvaluation, HRReport, InterviewEvaluation
from models.interview import (
    FollowUpDecision,
    InterviewPlan,
    InterviewQuestion,
    InterviewTranscript,
    InterviewTurn,
    NextPrompt,
)
from models.job import JobAnalysis, JobInput
from models.session import InterviewSession


# ---- common enums ----

def test_recommendation_level_matches_spec_allowed_language() -> None:
    assert {r.value for r in RecommendationLevel} == {
        "Strong evidence for human review",
        "Proceed to deeper technical assessment",
        "Requires additional validation",
        "Insufficient evidence from this interview",
    }


def test_interview_state_sequence() -> None:
    assert [s.value for s in InterviewState] == [
        "CREATED", "READY", "IN_PROGRESS", "COMPLETED", "EVALUATED",
    ]


def test_screening_outcome_is_separate_from_recommendation_language() -> None:
    # PASS/FAIL/NEEDS_REVIEW is an initial screening triage label, not an employment
    # decision -- it must never overlap with the recommendation's own vocabulary or
    # introduce a banned hiring-decision word.
    assert {o.value for o in ScreeningOutcome} == {"PASS", "FAIL", "NEEDS_REVIEW"}
    recommendation_values = {r.value for r in RecommendationLevel}
    outcome_values = {o.value for o in ScreeningOutcome}
    assert recommendation_values.isdisjoint(outcome_values)
    for value in outcome_values:
        assert "hire" not in value.lower()
        assert "reject" not in value.lower()
        assert "disqualif" not in value.lower()


# ---- job ----

def test_job_input_rejects_blank_fields() -> None:
    with pytest.raises(ValidationError):
        JobInput(company_name="  ", job_title="Engineer", description_text="text")
    with pytest.raises(ValidationError):
        JobInput(company_name="Acme", job_title="Engineer", description_text="   ")


def test_job_input_valid_and_trims_whitespace() -> None:
    job = JobInput(
        company_name="  FlairsTech  ",
        job_title="Junior AI Engineer",
        experience_level=ExperienceLevel.JUNIOR,
        description_text="Build things.",
    )
    assert job.company_name == "FlairsTech"
    assert job.experience_level == ExperienceLevel.JUNIOR


def test_job_analysis_defaults() -> None:
    analysis = JobAnalysis(job_title="Junior AI Engineer")
    assert analysis.experience_level is None
    assert analysis.required_skills == []
    assert analysis.role_summary == ""


def test_job_analysis_experience_level_is_free_text() -> None:
    analysis = JobAnalysis(job_title="Junior AI Engineer", experience_level="0-2 years, junior-leaning")
    assert analysis.experience_level == "0-2 years, junior-leaning"


# ---- candidate ----

def test_candidate_input_rejects_blank_cv() -> None:
    with pytest.raises(ValidationError):
        CandidateInput(cv_text="   ")


def test_candidate_analysis_and_fit_analysis_defaults() -> None:
    analysis = CandidateAnalysis()
    assert analysis.skills == []
    assert analysis.unclear_claims_to_validate == []
    fit = FitAnalysis()
    assert fit.missing_information == []


# ---- interview ----

def test_interview_question_rejects_blank_question() -> None:
    with pytest.raises(ValidationError):
        InterviewQuestion(
            id="q1", category=QuestionCategory.TECHNICAL, question="  ",
            purpose="assess", difficulty="medium",
        )


def test_interview_question_defaults_follow_up_allowed_true() -> None:
    q = InterviewQuestion(
        id="q1", category=QuestionCategory.TECHNICAL, question="Explain RAG.",
        purpose="assess technical depth", difficulty="medium",
    )
    assert q.follow_up_allowed is True


def _make_question(qid: str) -> InterviewQuestion:
    return InterviewQuestion(
        id=qid, category=QuestionCategory.TECHNICAL, question="Explain RAG.",
        purpose="assess technical depth", difficulty="medium",
    )


def test_interview_plan_rejects_empty_list() -> None:
    with pytest.raises(ValidationError):
        InterviewPlan(questions=[])


def test_interview_plan_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError):
        InterviewPlan(questions=[_make_question("q1"), _make_question("q1")])


def test_interview_plan_accepts_valid_questions() -> None:
    plan = InterviewPlan(questions=[_make_question("q1"), _make_question("q2")])
    assert len(plan.questions) == 2


def test_interview_transcript_filters_by_question_id() -> None:
    transcript = InterviewTranscript(
        turns=[
            InterviewTurn(question_id="q1", question="Q1", category=QuestionCategory.TECHNICAL,
                           answer="A1", is_follow_up=False, timestamp="2026-08-19T00:00:00Z"),
            InterviewTurn(question_id="q1", question="Q1 follow-up", category=QuestionCategory.TECHNICAL,
                           answer="A1b", is_follow_up=True, timestamp="2026-08-19T00:01:00Z"),
            InterviewTurn(question_id="q2", question="Q2", category=QuestionCategory.BEHAVIORAL,
                           answer="A2", is_follow_up=False, timestamp="2026-08-19T00:02:00Z"),
        ]
    )
    q1_turns = transcript.turns_for_question("q1")
    assert len(q1_turns) == 2
    assert all(t.question_id == "q1" for t in q1_turns)


def test_follow_up_decision_requires_question_when_asking() -> None:
    with pytest.raises(ValidationError):
        FollowUpDecision(should_ask=True, follow_up_question=None, reason="vague answer")


def test_follow_up_decision_valid_when_not_asking() -> None:
    decision = FollowUpDecision(should_ask=False, reason="answer was complete")
    assert decision.follow_up_question is None


def test_next_prompt_rejects_out_of_bounds_progress() -> None:
    with pytest.raises(ValidationError):
        NextPrompt(state=InterviewState.IN_PROGRESS, progress_pct=150.0)


# ---- evaluation ----

def test_category_evaluation_rejects_score_out_of_bounds() -> None:
    with pytest.raises(ValidationError):
        CategoryEvaluation(
            category=EvaluationCategory.TECHNICAL_KNOWLEDGE, score=101,
            sufficient_evidence=True, reasoning="x", evidence=["evidence"],
        )
    with pytest.raises(ValidationError):
        CategoryEvaluation(
            category=EvaluationCategory.TECHNICAL_KNOWLEDGE, score=-1,
            sufficient_evidence=True, reasoning="x", evidence=["evidence"],
        )


def test_category_evaluation_sufficient_evidence_requires_score() -> None:
    with pytest.raises(ValidationError):
        CategoryEvaluation(
            category=EvaluationCategory.TECHNICAL_KNOWLEDGE, score=None,
            sufficient_evidence=True, reasoning="x", evidence=["evidence"],
        )


def test_category_evaluation_sufficient_evidence_requires_nonempty_evidence() -> None:
    with pytest.raises(ValidationError):
        CategoryEvaluation(
            category=EvaluationCategory.TECHNICAL_KNOWLEDGE, score=80,
            sufficient_evidence=True, reasoning="x", evidence=[],
        )


def test_category_evaluation_insufficient_evidence_requires_null_score() -> None:
    with pytest.raises(ValidationError):
        CategoryEvaluation(
            category=EvaluationCategory.TECHNICAL_KNOWLEDGE, score=50,
            sufficient_evidence=False, reasoning="x", evidence=[],
        )


def test_category_evaluation_valid_insufficient_evidence_case() -> None:
    evaluation = CategoryEvaluation(
        category=EvaluationCategory.BEHAVIORAL_COMPETENCIES, score=None,
        sufficient_evidence=False, reasoning="Insufficient evidence", evidence=[],
    )
    assert evaluation.score is None
    assert evaluation.sufficient_evidence is False


def test_category_evaluation_normalizes_unscored_reasoning() -> None:
    evaluation = CategoryEvaluation(
        category=EvaluationCategory.COMMUNICATION,
        score=None,
        sufficient_evidence=False,
        reasoning="No useful response was available.",
        evidence=[],
    )
    assert evaluation.score is None
    assert evaluation.sufficient_evidence is False
    assert evaluation.reasoning == "Insufficient evidence"


def test_interview_evaluation_rejects_coverage_out_of_bounds() -> None:
    category_eval = CategoryEvaluation(
        category=EvaluationCategory.TECHNICAL_KNOWLEDGE, score=80,
        sufficient_evidence=True, reasoning="x", evidence=["e"],
    )
    with pytest.raises(ValidationError):
        InterviewEvaluation(
            category_evaluations=[category_eval], overall_score=80,
            evidence_coverage=1.5, recommendation=RecommendationLevel.PROCEED_TO_DEEPER_TECHNICAL_ASSESSMENT,
            screening_outcome=ScreeningOutcome.PASS, rubric_profile="technical",
        )


def test_hr_report_rejects_disallowed_recommendation_string() -> None:
    with pytest.raises(ValidationError):
        HRReport(
            session_id="s1", company="Acme", role="Engineer", candidate_overview="x",
            interview_summary="x", overall_score=80, evidence_coverage=0.9,
            category_scores=[], recommendation="Hire", screening_outcome=ScreeningOutcome.PASS,
            llm_provider="mock", is_mock=True, generated_at="2026-08-19T00:00:00Z",
        )


def test_hr_report_valid_construction() -> None:
    report = HRReport(
        session_id="s1", company="Acme", role="Engineer", candidate_overview="x",
        interview_summary="x", overall_score=80, evidence_coverage=0.9,
        category_scores=[], recommendation=RecommendationLevel.PROCEED_TO_DEEPER_TECHNICAL_ASSESSMENT,
        screening_outcome=ScreeningOutcome.PASS,
        llm_provider="mock", is_mock=True, generated_at="2026-08-19T00:00:00Z",
    )
    assert report.is_mock is True
    assert report.full_transcript == []
    assert report.screening_outcome == ScreeningOutcome.PASS


# ---- session ----

def test_interview_session_minimal_construction_defaults() -> None:
    session = InterviewSession(
        id="sess-1", company="FlairsTech", created_at="2026-08-19T00:00:00Z",
        updated_at="2026-08-19T00:00:00Z", llm_provider="mock", is_mock=True,
    )
    assert session.state == InterviewState.CREATED
    assert session.transcript.turns == []
    assert session.job_analysis is None
    assert session.current_follow_up_count == 0
