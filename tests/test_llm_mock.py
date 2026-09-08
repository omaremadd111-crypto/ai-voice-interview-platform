"""MockLLMService behavior: task routing, schema validity per task, error handling."""
import pytest

from models.candidate import CandidateAnalysis, FitAnalysis
from models.common import EvaluationCategory, QuestionCategory
from models.evaluation import CategoryEvaluation
from models.interview import FollowUpDecision, InterviewPlan, InterviewQuestion, InterviewTurn
from models.job import JobAnalysis
from models.voice_intent import VoiceIntent, VoiceIntentResult
from services.llm.base import LLMError, LLMRequest, UnsupportedTaskError
from services.llm.mock.mock_service import MOCK_DISCLAIMER, MockLLMService

JD_TEXT = (
    "We are looking for a Junior AI Engineer to build and maintain retrieval augmented "
    "generation (RAG) systems. You will design vector database pipelines, collaborate with "
    "the platform team, and write Python and SQL. Nice to have: experience with Docker and AWS."
)
CV_TEXT = (
    "Built a production RAG application using Python and a vector database for semantic "
    "search. I optimized retrieval latency because query volume grew 3x. 2 years of "
    "experience as a software engineer. Bachelor degree in Computer Science."
)


@pytest.fixture
def svc() -> MockLLMService:
    return MockLLMService()


@pytest.fixture
def job_analysis(svc: MockLLMService) -> JobAnalysis:
    request = LLMRequest(task="job_analysis", system="s", user="u", context={
        "job_title": "Junior AI Engineer", "experience_level": "Junior", "description_text": JD_TEXT,
    })
    return svc.generate_structured(request, JobAnalysis)


@pytest.fixture
def candidate_analysis(svc: MockLLMService) -> CandidateAnalysis:
    request = LLMRequest(task="candidate_analysis", system="s", user="u", context={"cv_text": CV_TEXT})
    return svc.generate_structured(request, CandidateAnalysis)


# ---- construction ----

def test_mock_service_identifies_itself_as_mock() -> None:
    svc = MockLLMService()
    assert svc.is_mock is True
    assert svc.provider_name == "mock"


# ---- job_analysis ----

def test_job_analysis_produces_valid_schema(job_analysis: JobAnalysis) -> None:
    assert isinstance(job_analysis, JobAnalysis)
    assert job_analysis.job_title == "Junior AI Engineer"
    assert "Python" in job_analysis.required_skills or "Python" in job_analysis.technical_topics


def test_job_analysis_splits_nice_to_have_after_marker(job_analysis: JobAnalysis) -> None:
    # "Nice to have: ... Docker and AWS" appears after the marker in JD_TEXT.
    assert "Cloud" in job_analysis.nice_to_have_skills or "Containers" in job_analysis.nice_to_have_skills
    assert "Python" not in job_analysis.nice_to_have_skills


def test_job_analysis_extracts_responsibilities_from_jd(job_analysis: JobAnalysis) -> None:
    assert len(job_analysis.responsibilities) > 0
    for sentence in job_analysis.responsibilities:
        assert sentence in JD_TEXT


def test_job_analysis_empty_description_yields_empty_lists(svc: MockLLMService) -> None:
    request = LLMRequest(task="job_analysis", system="s", user="u", context={
        "job_title": "Some Role", "description_text": "",
    })
    result = svc.generate_structured(request, JobAnalysis)
    assert result.required_skills == []
    assert result.technical_topics == []


# ---- candidate_analysis ----

def test_candidate_analysis_produces_valid_schema(candidate_analysis: CandidateAnalysis) -> None:
    assert isinstance(candidate_analysis, CandidateAnalysis)
    assert "Python" in candidate_analysis.skills


def test_candidate_analysis_flags_claims_worth_validating(candidate_analysis: CandidateAnalysis) -> None:
    # The CV claims a RAG application was "built" -- exactly the kind of claim SPEC says
    # should be treated as unverified, not assumed true.
    assert len(candidate_analysis.unclear_claims_to_validate) > 0
    for claim in candidate_analysis.unclear_claims_to_validate:
        assert claim in candidate_analysis.important_cv_claims


def test_candidate_analysis_claims_are_traceable_to_cv_text(candidate_analysis: CandidateAnalysis) -> None:
    for claim in candidate_analysis.important_cv_claims:
        assert claim in CV_TEXT


# ---- fit_analysis ----

def test_fit_analysis_produces_valid_schema(svc: MockLLMService, job_analysis: JobAnalysis,
                                             candidate_analysis: CandidateAnalysis) -> None:
    request = LLMRequest(task="fit_analysis", system="s", user="u", context={
        "job_analysis": job_analysis, "candidate_analysis": candidate_analysis,
    })
    result = svc.generate_structured(request, FitAnalysis)
    assert isinstance(result, FitAnalysis)
    assert "Python" in result.strong_alignment_areas


def test_fit_analysis_identifies_missing_information(svc: MockLLMService, job_analysis: JobAnalysis,
                                                       candidate_analysis: CandidateAnalysis) -> None:
    request = LLMRequest(task="fit_analysis", system="s", user="u", context={
        "job_analysis": job_analysis, "candidate_analysis": candidate_analysis,
    })
    result = svc.generate_structured(request, FitAnalysis)
    # The JD requires SQL; the CV never mentions it.
    assert "SQL" in result.missing_information


def test_fit_analysis_accepts_plain_dict_context(svc: MockLLMService) -> None:
    request = LLMRequest(task="fit_analysis", system="s", user="u", context={
        "job_analysis": {"required_skills": ["Python"], "nice_to_have_skills": []},
        "candidate_analysis": {"skills": ["Python"], "technologies": ["Python"]},
    })
    result = svc.generate_structured(request, FitAnalysis)
    assert result.strong_alignment_areas == ["Python"]


# ---- interview_planning ----

def test_interview_planning_produces_valid_schema(svc: MockLLMService, job_analysis: JobAnalysis,
                                                    candidate_analysis: CandidateAnalysis) -> None:
    fit_request = LLMRequest(task="fit_analysis", system="s", user="u", context={
        "job_analysis": job_analysis, "candidate_analysis": candidate_analysis,
    })
    fit_analysis = svc.generate_structured(fit_request, FitAnalysis)

    request = LLMRequest(task="interview_planning", system="s", user="u", context={
        "job_title": "Junior AI Engineer", "company_name": "FlairsTech", "experience_level": "Junior",
        "num_questions": 6, "job_analysis": job_analysis, "candidate_analysis": candidate_analysis,
        "fit_analysis": fit_analysis,
    })
    plan = svc.generate_structured(request, InterviewPlan)
    assert isinstance(plan, InterviewPlan)
    assert len(plan.questions) == 6
    categories = [q.category for q in plan.questions]
    assert categories[0] == QuestionCategory.INTRODUCTION
    assert categories[-1] == QuestionCategory.CLOSING
    ids = [q.id for q in plan.questions]
    assert len(ids) == len(set(ids))


def test_interview_planning_questions_are_not_generic(svc: MockLLMService, job_analysis: JobAnalysis,
                                                        candidate_analysis: CandidateAnalysis) -> None:
    fit_request = LLMRequest(task="fit_analysis", system="s", user="u", context={
        "job_analysis": job_analysis, "candidate_analysis": candidate_analysis,
    })
    fit_analysis = svc.generate_structured(fit_request, FitAnalysis)
    request = LLMRequest(task="interview_planning", system="s", user="u", context={
        "job_title": "Junior AI Engineer", "company_name": "FlairsTech", "experience_level": "Junior",
        "num_questions": 6, "job_analysis": job_analysis, "candidate_analysis": candidate_analysis,
        "fit_analysis": fit_analysis,
    })
    plan = svc.generate_structured(request, InterviewPlan)
    technical_questions = [q for q in plan.questions if q.category == QuestionCategory.TECHNICAL]
    assert len(technical_questions) >= 1
    # Grounded in this specific JD/CV pair, not a generic placeholder.
    assert any(
        any(skill.lower() in q.question.lower() for skill in job_analysis.technical_topics)
        for q in technical_questions
    )


def test_interview_planning_enforces_minimum_of_three_questions(svc: MockLLMService) -> None:
    request = LLMRequest(task="interview_planning", system="s", user="u", context={
        "job_title": "Role", "company_name": "Acme", "num_questions": 1,
    })
    plan = svc.generate_structured(request, InterviewPlan)
    assert len(plan.questions) >= 3


# ---- follow_up ----

def test_follow_up_asks_when_expected_topic_missing(svc: MockLLMService) -> None:
    question = InterviewQuestion(
        id="q1", category=QuestionCategory.TECHNICAL, question="Explain your RAG work.",
        purpose="assess", expected_topics=["retrieval strategy", "vector database"],
        difficulty="medium", follow_up_allowed=True,
    )
    request = LLMRequest(task="follow_up", system="s", user="u", context={
        "question": question, "latest_answer": "I worked on a RAG system, it was good.",
    })
    decision = svc.generate_structured(request, FollowUpDecision)
    assert decision.should_ask is True
    assert decision.follow_up_question is not None


def test_follow_up_declines_when_answer_is_thorough(svc: MockLLMService) -> None:
    question = InterviewQuestion(
        id="q1", category=QuestionCategory.TECHNICAL, question="Explain your RAG work.",
        purpose="assess", expected_topics=["retrieval strategy"],
        difficulty="medium", follow_up_allowed=True,
    )
    request = LLMRequest(task="follow_up", system="s", user="u", context={
        "question": question,
        "latest_answer": (
            "I designed the retrieval strategy using hybrid search because keyword search "
            "missed 30% of relevant documents, so I combined it with semantic search."
        ),
    })
    decision = svc.generate_structured(request, FollowUpDecision)
    assert decision.should_ask is False


def test_follow_up_declines_on_refusal(svc: MockLLMService) -> None:
    question = InterviewQuestion(
        id="q1", category=QuestionCategory.TECHNICAL, question="Explain your RAG work.",
        purpose="assess", expected_topics=["retrieval strategy"],
        difficulty="medium", follow_up_allowed=True,
    )
    request = LLMRequest(task="follow_up", system="s", user="u", context={
        "question": question, "latest_answer": "I don't know.",
    })
    decision = svc.generate_structured(request, FollowUpDecision)
    assert decision.should_ask is False


def test_follow_up_declines_when_category_disallows_it(svc: MockLLMService) -> None:
    question = InterviewQuestion(
        id="q1", category=QuestionCategory.INTRODUCTION, question="Introduce yourself.",
        purpose="warm-up", expected_topics=[], difficulty="easy", follow_up_allowed=False,
    )
    request = LLMRequest(task="follow_up", system="s", user="u", context={
        "question": question, "latest_answer": "Hi, I'm a candidate.",
    })
    decision = svc.generate_structured(request, FollowUpDecision)
    assert decision.should_ask is False


# ---- evaluation ----

def _turn(answer: str) -> InterviewTurn:
    return InterviewTurn(
        question_id="q1", question="Explain your RAG work.", category=QuestionCategory.TECHNICAL,
        answer=answer, is_follow_up=False, timestamp="2026-08-19T00:00:00Z",
    )


def test_evaluation_produces_scored_result_with_evidence(svc: MockLLMService) -> None:
    turns = [_turn(
        "I designed the retrieval strategy using hybrid search with a vector database because "
        "keyword search missed relevant documents. I evaluated it against a ground truth set."
    )]
    request = LLMRequest(task="evaluation", system="s", user="u", context={
        "category": EvaluationCategory.TECHNICAL_KNOWLEDGE,
        "expected_topics": ["retrieval strategy", "vector database"],
        "turns": turns,
    })
    result = svc.generate_structured(request, CategoryEvaluation)
    assert isinstance(result, CategoryEvaluation)
    assert result.sufficient_evidence is True
    assert result.score is not None
    assert 0 <= result.score <= 100
    assert len(result.evidence) > 0
    for item in result.evidence:
        stripped = item[:-1] if item.endswith("…") else item
        assert stripped in turns[0].answer


def test_evaluation_returns_insufficient_evidence_for_empty_transcript(svc: MockLLMService) -> None:
    request = LLMRequest(task="evaluation", system="s", user="u", context={
        "category": EvaluationCategory.TECHNICAL_KNOWLEDGE,
        "expected_topics": ["retrieval strategy"],
        "turns": [_turn("")],
    })
    result = svc.generate_structured(request, CategoryEvaluation)
    assert result.sufficient_evidence is False
    assert result.score is None
    assert result.evidence == []
    assert "insufficient evidence" in result.reasoning.lower()


def test_evaluation_returns_insufficient_evidence_for_no_turns(svc: MockLLMService) -> None:
    request = LLMRequest(task="evaluation", system="s", user="u", context={
        "category": EvaluationCategory.COMMUNICATION, "expected_topics": [], "turns": [],
    })
    result = svc.generate_structured(request, CategoryEvaluation)
    assert result.sufficient_evidence is False
    assert result.score is None


def test_evaluation_accepts_plain_dict_turns(svc: MockLLMService) -> None:
    request = LLMRequest(task="evaluation", system="s", user="u", context={
        "category": "technical_knowledge",
        "expected_topics": [],
        "turns": [{
            "question_id": "q1", "question": "Q", "category": "technical",
            "answer": "I built and deployed a system that reduced latency by 40% because caching helped.",
            "is_follow_up": False, "timestamp": "2026-08-19T00:00:00Z",
        }],
    })
    result = svc.generate_structured(request, CategoryEvaluation)
    assert result.sufficient_evidence is True


# ---- voice_intent (Phase 1: Mock Mode must demonstrate the same fix, offline) ----

def _voice_intent_request(utterance: str) -> LLMRequest:
    return LLMRequest(task="voice_intent", system="s", user="u", context={
        "question": {"expected_topics": []}, "latest_answer": utterance,
    })


@pytest.mark.parametrize("utterance", [
    "I don't know.",
    "I haven't worked with that.",
    "I'm not familiar with Kubernetes.",
    "I don't really have experience with this.",
    "I haven't had the chance to use that technology.",
    "That's not something I've worked on before.",
])
def test_mock_voice_intent_recognises_dont_know_paraphrases(svc: MockLLMService, utterance: str) -> None:
    result = svc.generate_structured(_voice_intent_request(utterance), VoiceIntentResult)
    assert result.intent is VoiceIntent.DONT_KNOW
    assert result.reaction is not None


def test_mock_voice_intent_recognises_genuine_refusal_distinctly(svc: MockLLMService) -> None:
    result = svc.generate_structured(
        _voice_intent_request("I'd rather not discuss that."), VoiceIntentResult,
    )
    assert result.intent is VoiceIntent.GENUINE_REFUSAL


def test_mock_voice_intent_hedge_then_substance_is_not_dont_know(svc: MockLLMService) -> None:
    result = svc.generate_structured(
        _voice_intent_request(
            "I'm not sure of the exact number, but we handled around two million requests."
        ),
        VoiceIntentResult,
    )
    assert result.intent in {VoiceIntent.SUBSTANTIVE_ANSWER, VoiceIntent.PARTIAL_ANSWER}


def test_mock_voice_intent_substantive_answer_gets_content_caused_reaction(svc: MockLLMService) -> None:
    # Phase 3: reactions are content-caused, not refusal-only. The mock supplies
    # a short safe acknowledgement for substantive answers.
    result = svc.generate_structured(
        _voice_intent_request(
            "I led the migration of our payments service onto Kubernetes and cut deploy time by 40%."
        ),
        VoiceIntentResult,
    )
    assert result.intent is VoiceIntent.SUBSTANTIVE_ANSWER
    assert result.reaction is not None
    assert len(result.reaction.split()) <= 6
    assert "thank you for sharing" not in result.reaction.lower()


# ---- unsupported task / schema mismatch ----

def test_unsupported_task_raises(svc: MockLLMService) -> None:
    request = LLMRequest(task="not_a_real_task", system="s", user="u", context={})
    with pytest.raises(UnsupportedTaskError):
        svc.generate_structured(request, JobAnalysis)


def test_unsupported_task_raises_for_generate_text(svc: MockLLMService) -> None:
    request = LLMRequest(task="not_a_real_task", system="s", user="u", context={})
    with pytest.raises(UnsupportedTaskError):
        svc.generate_text(request)


def test_schema_mismatch_raises_llm_error(svc: MockLLMService) -> None:
    request = LLMRequest(task="job_analysis", system="s", user="u", context={
        "job_title": "Role", "description_text": "text",
    })
    with pytest.raises(LLMError):
        svc.generate_structured(request, CandidateAnalysis)


# ---- generate_text ----

def test_generate_text_includes_mock_disclaimer(svc: MockLLMService) -> None:
    request = LLMRequest(task="job_analysis", system="s", user="u", context={
        "job_title": "Role", "description_text": JD_TEXT,
    })
    text = svc.generate_text(request)
    assert MOCK_DISCLAIMER in text
    assert "Role" in text
