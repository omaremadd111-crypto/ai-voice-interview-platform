"""Determinism guarantee: identical LLMRequest input always produces identical
structured output, across completely fresh MockLLMService instances."""
from models.candidate import CandidateAnalysis, FitAnalysis
from models.common import EvaluationCategory, QuestionCategory
from models.evaluation import CategoryEvaluation
from models.interview import FollowUpDecision, InterviewPlan, InterviewQuestion, InterviewTurn
from models.job import JobAnalysis
from services.llm.base import LLMRequest
from services.llm.mock.mock_service import MockLLMService

JD_TEXT = (
    "We are looking for a Junior AI Engineer to build and maintain retrieval augmented "
    "generation (RAG) systems. You will design vector database pipelines, collaborate with "
    "the platform team, and write Python and SQL. Nice to have: experience with Docker and AWS."
)
CV_TEXT = (
    "Built a production RAG application using Python and a vector database for semantic "
    "search. I optimized retrieval latency because query volume grew 3x. 2 years of "
    "experience as a software engineer."
)


def test_job_analysis_deterministic_across_fresh_instances() -> None:
    request = LLMRequest(task="job_analysis", system="s", user="u", context={
        "job_title": "Junior AI Engineer", "experience_level": "Junior", "description_text": JD_TEXT,
    })
    first = MockLLMService().generate_structured(request, JobAnalysis)
    second = MockLLMService().generate_structured(request, JobAnalysis)
    assert first == second
    assert first.model_dump() == second.model_dump()


def test_candidate_analysis_deterministic_across_fresh_instances() -> None:
    request = LLMRequest(task="candidate_analysis", system="s", user="u", context={"cv_text": CV_TEXT})
    first = MockLLMService().generate_structured(request, CandidateAnalysis)
    second = MockLLMService().generate_structured(request, CandidateAnalysis)
    assert first == second


def test_fit_analysis_deterministic_across_fresh_instances() -> None:
    job_analysis = MockLLMService().generate_structured(
        LLMRequest(task="job_analysis", system="s", user="u", context={
            "job_title": "Junior AI Engineer", "description_text": JD_TEXT,
        }),
        JobAnalysis,
    )
    candidate_analysis = MockLLMService().generate_structured(
        LLMRequest(task="candidate_analysis", system="s", user="u", context={"cv_text": CV_TEXT}),
        CandidateAnalysis,
    )
    request = LLMRequest(task="fit_analysis", system="s", user="u", context={
        "job_analysis": job_analysis, "candidate_analysis": candidate_analysis,
    })
    first = MockLLMService().generate_structured(request, FitAnalysis)
    second = MockLLMService().generate_structured(request, FitAnalysis)
    assert first == second


def test_interview_planning_deterministic_across_fresh_instances() -> None:
    request = LLMRequest(task="interview_planning", system="s", user="u", context={
        "job_title": "Junior AI Engineer", "company_name": "FlairsTech", "experience_level": "Junior",
        "num_questions": 7,
        "job_analysis": {"technical_topics": ["Python", "RAG"], "required_skills": ["Python"],
                          "responsibilities": [], "behavioral_competencies": []},
        "candidate_analysis": {"unclear_claims_to_validate": [], "important_cv_claims": []},
        "fit_analysis": {"missing_information": [], "questions_to_investigate": []},
    })
    first = MockLLMService().generate_structured(request, InterviewPlan)
    second = MockLLMService().generate_structured(request, InterviewPlan)
    assert first == second
    assert [q.id for q in first.questions] == [q.id for q in second.questions]


def test_follow_up_deterministic_across_fresh_instances() -> None:
    question = InterviewQuestion(
        id="q1", category=QuestionCategory.TECHNICAL, question="Explain your RAG work.",
        purpose="assess", expected_topics=["retrieval strategy", "vector database"],
        difficulty="medium", follow_up_allowed=True,
    )
    request = LLMRequest(task="follow_up", system="s", user="u", context={
        "question": question, "latest_answer": "I worked on a RAG system, it was good.",
    })
    first = MockLLMService().generate_structured(request, FollowUpDecision)
    second = MockLLMService().generate_structured(request, FollowUpDecision)
    assert first == second


def test_evaluation_deterministic_across_fresh_instances() -> None:
    turns = [InterviewTurn(
        question_id="q1", question="Explain your RAG work.", category=QuestionCategory.TECHNICAL,
        answer="I designed the retrieval strategy using hybrid search with a vector database "
               "because keyword search missed relevant documents.",
        is_follow_up=False, timestamp="2026-08-19T00:00:00Z",
    )]
    request = LLMRequest(task="evaluation", system="s", user="u", context={
        "category": EvaluationCategory.TECHNICAL_KNOWLEDGE,
        "expected_topics": ["retrieval strategy", "vector database"],
        "turns": turns,
    })
    first = MockLLMService().generate_structured(request, CategoryEvaluation)
    second = MockLLMService().generate_structured(request, CategoryEvaluation)
    assert first == second
    assert first.score == second.score
    assert first.evidence == second.evidence


def test_repeated_calls_on_the_same_instance_are_also_deterministic() -> None:
    svc = MockLLMService()
    request = LLMRequest(task="candidate_analysis", system="s", user="u", context={"cv_text": CV_TEXT})
    results = [svc.generate_structured(request, CandidateAnalysis) for _ in range(5)]
    assert all(r == results[0] for r in results)
