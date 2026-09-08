"""End-to-end evaluation pipeline: a completed interview (via Phase 5's engine)
flows through Evaluator -> scoring -> report_service, and a demonstrably strong
candidate scores meaningfully higher than a demonstrably weak one.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.candidate_analyzer import CandidateAnalyzer
from agents.evaluator import Evaluator
from agents.fit_analyzer import FitAnalyzer
from agents.interview_planner import InterviewPlanner
from agents.interviewer import Interviewer
from agents.job_analyzer import JobAnalyzer
from config.rubric_config import load_rubric_config
from models.common import InterviewState
from models.interview import InterviewPlan, InterviewQuestion
from models.session import InterviewSession
from services.interview_engine import InterviewEngine
from services.llm.mock.mock_service import MockLLMService
from services.report_service import build_hr_report
from services.scoring import build_interview_evaluation

# Fictional demo data for testing only -- not an actual FlairsTech vacancy or candidate.
JD_TEXT = (
    "FlairsTech is hiring a Junior AI Engineer to help build and maintain retrieval "
    "augmented generation (RAG) systems for internal tooling. Responsibilities include "
    "designing vector database pipelines, writing Python and SQL, and collaborating with "
    "the platform team on system design. Nice to have: experience with Docker and AWS."
)
CV_TEXT = (
    "Built a production RAG application using Python and a vector database for semantic "
    "search. I optimized retrieval latency because query volume grew 3x. 2 years of "
    "experience as a software engineer."
)


def _strong_answer_for(question: InterviewQuestion) -> str:
    # Explicitly mentions every expected topic, guaranteeing high coverage regardless
    # of which specific topics the planner happened to seed this question with.
    topics = question.expected_topics or ["this area"]
    topic_text = " and ".join(t.lower() for t in topics)
    return (
        f"I have direct, hands-on experience with {topic_text} because I worked on it "
        f"personally. For example, I built and optimized a solution involving {topic_text}, "
        f"which improved outcomes by roughly 35 percent, so that the team could rely on it."
    )


def _weak_answer_for(_question: InterviewQuestion) -> str:
    return "I don't know."


def _run_full_interview(plan: InterviewPlan, llm_service: MockLLMService, answer_fn, session_id: str) -> InterviewSession:
    now = datetime.now(timezone.utc).isoformat()
    session = InterviewSession(
        id=session_id, company="FlairsTech", interview_plan=plan, state=InterviewState.READY,
        created_at=now, updated_at=now, llm_provider="mock", is_mock=True,
    )
    engine = InterviewEngine(Interviewer(llm_service))
    prompt = engine.start(session)
    safety_counter = 0
    while not prompt.finished:
        current_question = plan.questions[prompt.question_index]
        answer = answer_fn(current_question)
        prompt = engine.submit_answer(session, answer)
        safety_counter += 1
        assert safety_counter < 50
    return session


@pytest.fixture(scope="module")
def shared_plan() -> InterviewPlan:
    llm_service = MockLLMService()
    job_analysis = JobAnalyzer(llm_service).analyze("Junior AI Engineer", "Junior", JD_TEXT)
    candidate_analysis = CandidateAnalyzer(llm_service).analyze(CV_TEXT)
    fit_analysis = FitAnalyzer(llm_service).analyze(job_analysis, candidate_analysis)
    return InterviewPlanner(llm_service).plan(
        "Junior AI Engineer", "FlairsTech", "Junior", 6, job_analysis, candidate_analysis, fit_analysis,
    )


def test_strong_candidate_scores_higher_than_weak_candidate(shared_plan: InterviewPlan) -> None:
    llm_service = MockLLMService()
    evaluator = Evaluator(llm_service)
    rubric_config = load_rubric_config(Path("config/rubrics.json"))
    profile = rubric_config.get_profile(rubric_config.default_profile)

    strong_session = _run_full_interview(shared_plan, llm_service, _strong_answer_for, "strong-session")
    weak_session = _run_full_interview(shared_plan, llm_service, _weak_answer_for, "weak-session")

    strong_categories = evaluator.evaluate_all_categories(shared_plan, strong_session.transcript, "strong-session")
    weak_categories = evaluator.evaluate_all_categories(shared_plan, weak_session.transcript, "weak-session")

    strong_eval = build_interview_evaluation(
        strong_categories, profile, rubric_config.default_profile, rubric_config.thresholds, "strong-session",
    )
    weak_eval = build_interview_evaluation(
        weak_categories, profile, rubric_config.default_profile, rubric_config.thresholds, "weak-session",
    )

    assert strong_eval.overall_score is not None
    assert weak_eval.overall_score is None
    assert strong_eval.evidence_coverage > weak_eval.evidence_coverage

    strong_report = build_hr_report(strong_session, strong_eval)
    weak_report = build_hr_report(weak_session, weak_eval)

    assert strong_report.recommendation != weak_report.recommendation
    assert len(strong_report.strong_evidence) > 0
    assert len(weak_report.strong_evidence) == 0
    assert len(weak_report.areas_requiring_validation) > 0


def test_full_pipeline_produces_a_saveable_report(shared_plan: InterviewPlan, tmp_path: Path) -> None:
    llm_service = MockLLMService()
    session = _run_full_interview(shared_plan, llm_service, _strong_answer_for, "save-test-session")
    evaluator = Evaluator(llm_service)
    rubric_config = load_rubric_config(Path("config/rubrics.json"))
    profile = rubric_config.get_profile(rubric_config.default_profile)

    categories = evaluator.evaluate_all_categories(shared_plan, session.transcript, session.id)
    evaluation = build_interview_evaluation(
        categories, profile, rubric_config.default_profile, rubric_config.thresholds, session.id,
    )
    report = build_hr_report(session, evaluation)

    from services.report_service import save_report
    path = save_report(report, tmp_path)
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("# HR Interview Report")


def test_pipeline_is_deterministic_across_fresh_runs(shared_plan: InterviewPlan) -> None:
    def run() -> str:
        llm_service = MockLLMService()
        session = _run_full_interview(shared_plan, llm_service, _strong_answer_for, "determinism-session")
        evaluator = Evaluator(llm_service)
        rubric_config = load_rubric_config(Path("config/rubrics.json"))
        profile = rubric_config.get_profile(rubric_config.default_profile)
        categories = evaluator.evaluate_all_categories(shared_plan, session.transcript, session.id)
        evaluation = build_interview_evaluation(
            categories, profile, rubric_config.default_profile, rubric_config.thresholds, session.id,
        )
        return f"{evaluation.overall_score}|{evaluation.evidence_coverage}|{evaluation.recommendation.value}"

    assert run() == run()
