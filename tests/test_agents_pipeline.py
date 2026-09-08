"""End-to-end agent pipeline: JD + CV -> JobAnalysis -> CandidateAnalysis -> FitAnalysis
-> InterviewPlan, chained exactly as the future InterviewAgentService will call them.

The FlairsTech / Junior AI Engineer content below is fictional demo data for testing
only -- it does not represent an actual FlairsTech vacancy, requirement, or candidate.
"""
import pytest

from agents.candidate_analyzer import CandidateAnalyzer
from agents.fit_analyzer import FitAnalyzer
from agents.interview_planner import InterviewPlanner
from agents.job_analyzer import JobAnalyzer
from models.candidate import CandidateAnalysis, FitAnalysis
from models.common import QuestionCategory
from models.interview import InterviewPlan
from models.job import JobAnalysis
from services.llm.mock.mock_service import MockLLMService

DEMO_JD_TEXT = (
    "FlairsTech is hiring a Junior AI Engineer to help build and maintain retrieval "
    "augmented generation (RAG) systems for internal tooling. Responsibilities include "
    "designing vector database pipelines, writing Python and SQL, and collaborating with "
    "the platform team on system design. Nice to have: experience with Docker and AWS."
)
DEMO_CV_TEXT = (
    "Built a production RAG application using Python and a vector database for semantic "
    "search. I optimized retrieval latency because query volume grew 3x. 2 years of "
    "experience as a software engineer. Bachelor degree in Computer Science."
)


@pytest.fixture
def llm_service() -> MockLLMService:
    return MockLLMService()


def test_full_pipeline_produces_a_coherent_candidate_specific_plan(llm_service: MockLLMService) -> None:
    job_analyzer = JobAnalyzer(llm_service)
    candidate_analyzer = CandidateAnalyzer(llm_service)
    fit_analyzer = FitAnalyzer(llm_service)
    planner = InterviewPlanner(llm_service)

    job_analysis = job_analyzer.analyze("Junior AI Engineer", "Junior", DEMO_JD_TEXT)
    assert isinstance(job_analysis, JobAnalysis)

    candidate_analysis = candidate_analyzer.analyze(DEMO_CV_TEXT)
    assert isinstance(candidate_analysis, CandidateAnalysis)

    fit_analysis = fit_analyzer.analyze(job_analysis, candidate_analysis)
    assert isinstance(fit_analysis, FitAnalysis)
    # The JD requires SQL; the CV never mentions it -- missing information, not a
    # confirmed gap, and it should surface as something worth investigating.
    assert "SQL" in fit_analysis.missing_information

    plan = planner.plan(
        "Junior AI Engineer", "FlairsTech", "Junior", 7,
        job_analysis, candidate_analysis, fit_analysis,
    )
    assert isinstance(plan, InterviewPlan)
    assert len(plan.questions) == 7

    texts = [q.question for q in plan.questions]
    assert len(texts) == len(set(texts))  # no duplicates end-to-end

    categories = {q.category for q in plan.questions}
    assert QuestionCategory.CV_PROJECT_VALIDATION in categories  # validates the RAG claim
    assert QuestionCategory.TECHNICAL in categories  # probes required skills / gaps

    technical_questions = [q for q in plan.questions if q.category == QuestionCategory.TECHNICAL]
    assert any("SQL" in q.question for q in technical_questions)  # the actual gap, not a guess

    validation_questions = [q for q in plan.questions if q.category == QuestionCategory.CV_PROJECT_VALIDATION]
    assert len(validation_questions) >= 1
    assert any(
        any(claim in q.question for claim in candidate_analysis.unclear_claims_to_validate)
        for q in validation_questions
    )


def test_full_pipeline_is_deterministic_across_fresh_services() -> None:
    def run_pipeline() -> InterviewPlan:
        llm_service = MockLLMService()
        job_analysis = JobAnalyzer(llm_service).analyze("Junior AI Engineer", "Junior", DEMO_JD_TEXT)
        candidate_analysis = CandidateAnalyzer(llm_service).analyze(DEMO_CV_TEXT)
        fit_analysis = FitAnalyzer(llm_service).analyze(job_analysis, candidate_analysis)
        return InterviewPlanner(llm_service).plan(
            "Junior AI Engineer", "FlairsTech", "Junior", 7,
            job_analysis, candidate_analysis, fit_analysis,
        )

    first = run_pipeline()
    second = run_pipeline()
    assert first == second


def test_weak_candidate_surfaces_more_missing_information_than_strong_candidate(
    llm_service: MockLLMService,
) -> None:
    job_analysis = JobAnalyzer(llm_service).analyze("Junior AI Engineer", "Junior", DEMO_JD_TEXT)

    strong_cv = DEMO_CV_TEXT
    weak_cv = "Recently graduated. Interested in learning software engineering."

    strong_candidate = CandidateAnalyzer(llm_service).analyze(strong_cv)
    weak_candidate = CandidateAnalyzer(llm_service).analyze(weak_cv)

    strong_fit = FitAnalyzer(llm_service).analyze(job_analysis, strong_candidate)
    weak_fit = FitAnalyzer(llm_service).analyze(job_analysis, weak_candidate)

    assert len(weak_fit.missing_information) >= len(strong_fit.missing_information)
    assert len(weak_fit.strong_alignment_areas) <= len(strong_fit.strong_alignment_areas)
