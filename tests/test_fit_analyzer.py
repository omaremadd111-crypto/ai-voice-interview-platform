"""FitAnalyzer agent tests: alignment vs. missing-information vs. confirmed-absence."""
import pytest

from agents.fit_analyzer import FitAnalyzer
from models.candidate import CandidateAnalysis, FitAnalysis
from models.job import JobAnalysis
from services.llm.mock.mock_service import MockLLMService

_BANNED_ABSENCE_PHRASES = ("does not have", "doesn't have", "lacks", "cannot", "can't", "definitely does not")
_BANNED_DECISION_WORDS = ("hire", "reject", "disqualif")


@pytest.fixture
def analyzer() -> FitAnalyzer:
    return FitAnalyzer(MockLLMService())


@pytest.fixture
def job_analysis() -> JobAnalysis:
    return JobAnalysis(
        job_title="Junior AI Engineer",
        experience_level="Junior",
        required_skills=["Python", "SQL", "RAG"],
        nice_to_have_skills=["Cloud"],
        responsibilities=["Build and maintain RAG systems."],
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


def test_returns_valid_fit_analysis(analyzer: FitAnalyzer, job_analysis: JobAnalysis,
                                     candidate_analysis: CandidateAnalysis) -> None:
    result = analyzer.analyze(job_analysis, candidate_analysis)
    assert isinstance(result, FitAnalysis)


def test_identifies_confirmed_alignment(analyzer: FitAnalyzer, job_analysis: JobAnalysis,
                                         candidate_analysis: CandidateAnalysis) -> None:
    result = analyzer.analyze(job_analysis, candidate_analysis)
    # Both job and CV evidence Python and RAG -- that is confirmed alignment.
    assert "Python" in result.strong_alignment_areas
    assert "RAG" in result.strong_alignment_areas


def test_distinguishes_missing_information_from_alignment(analyzer: FitAnalyzer, job_analysis: JobAnalysis,
                                                            candidate_analysis: CandidateAnalysis) -> None:
    result = analyzer.analyze(job_analysis, candidate_analysis)
    # SQL is required by the job but never mentioned anywhere in the candidate's CV.
    assert "SQL" in result.missing_information
    assert "SQL" not in result.strong_alignment_areas


def test_missing_information_is_not_phrased_as_confirmed_absence(analyzer: FitAnalyzer, job_analysis: JobAnalysis,
                                                                   candidate_analysis: CandidateAnalysis) -> None:
    result = analyzer.analyze(job_analysis, candidate_analysis)
    for item in result.missing_information:
        lower = item.lower()
        for phrase in _BANNED_ABSENCE_PHRASES:
            assert phrase not in lower, f"missing_information item asserts confirmed absence: {item!r}"


def test_does_not_assume_unmentioned_skill_is_definitely_absent(analyzer: FitAnalyzer, job_analysis: JobAnalysis,
                                                                  candidate_analysis: CandidateAnalysis) -> None:
    result = analyzer.analyze(job_analysis, candidate_analysis)
    all_text = " ".join(
        result.strong_alignment_areas + result.relevant_candidate_experience
        + result.important_job_requirements + result.skills_requiring_validation
        + result.missing_information + result.questions_to_investigate
    ).lower()
    for phrase in _BANNED_ABSENCE_PHRASES:
        assert phrase not in all_text


def test_does_not_automatically_reject(analyzer: FitAnalyzer, job_analysis: JobAnalysis,
                                        candidate_analysis: CandidateAnalysis) -> None:
    result = analyzer.analyze(job_analysis, candidate_analysis)
    assert not hasattr(result, "recommendation")
    assert not hasattr(result, "reject")
    all_text = " ".join(
        result.strong_alignment_areas + result.relevant_candidate_experience
        + result.important_job_requirements + result.skills_requiring_validation
        + result.missing_information + result.questions_to_investigate
    ).lower()
    for word in _BANNED_DECISION_WORDS:
        assert word not in all_text


def test_flags_unclear_claims_for_validation(analyzer: FitAnalyzer, job_analysis: JobAnalysis,
                                              candidate_analysis: CandidateAnalysis) -> None:
    result = analyzer.analyze(job_analysis, candidate_analysis)
    assert "Built a production RAG application." in result.skills_requiring_validation


def test_produces_investigatable_questions_grounded_in_cv(analyzer: FitAnalyzer, job_analysis: JobAnalysis,
                                                            candidate_analysis: CandidateAnalysis) -> None:
    result = analyzer.analyze(job_analysis, candidate_analysis)
    assert len(result.questions_to_investigate) > 0
