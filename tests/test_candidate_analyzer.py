"""CandidateAnalyzer agent tests, run against MockLLMService (no API key required)."""
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel

from agents.candidate_analyzer import CandidateAnalyzer
from models.candidate import CandidateAnalysis
from services.llm.base import LLMRequest
from services.llm.mock.mock_service import MockLLMService

T = TypeVar("T", bound=BaseModel)

# Fictional demo content for testing only -- not a real candidate.
CV_TEXT = (
    "Built a production RAG application using Python and a vector database for semantic "
    "search. I optimized retrieval latency because query volume grew 3x. 2 years of "
    "experience as a software engineer. Bachelor degree in Computer Science."
)


@pytest.fixture
def analyzer() -> CandidateAnalyzer:
    return CandidateAnalyzer(MockLLMService())


def test_returns_valid_candidate_analysis(analyzer: CandidateAnalyzer) -> None:
    result = analyzer.analyze(CV_TEXT)
    assert isinstance(result, CandidateAnalysis)
    assert "Python" in result.skills


def test_extracts_explicit_candidate_name_from_cv_heading(analyzer: CandidateAnalyzer) -> None:
    result = analyzer.analyze(f"# Jordan Rivera\n\n{CV_TEXT}")
    assert result.full_name == "Jordan Rivera"


def test_explicit_cv_heading_fills_name_when_provider_omits_it() -> None:
    class _NameOmittingMock(MockLLMService):
        def generate_structured(self, request: LLMRequest, schema: type[T]) -> T:
            result = super().generate_structured(request, schema)
            if isinstance(result, CandidateAnalysis):
                return cast(T, result.model_copy(update={"full_name": None}, deep=True))
            return result

    result = CandidateAnalyzer(_NameOmittingMock()).analyze(
        f"# Jordan Rivera\n\n{CV_TEXT}"
    )
    assert result.full_name == "Jordan Rivera"


def test_candidate_name_is_not_inferred_from_email_or_role_heading(
    analyzer: CandidateAnalyzer,
) -> None:
    result = analyzer.analyze(
        f"jordan.rivera@example.test\nSoftware Engineer\n\n{CV_TEXT}"
    )
    assert result.full_name is None


def test_extracts_relevant_cv_claims(analyzer: CandidateAnalyzer) -> None:
    result = analyzer.analyze(CV_TEXT)
    assert len(result.important_cv_claims) > 0
    for claim in result.important_cv_claims:
        assert claim in CV_TEXT  # traceable to the actual CV, never invented


def test_identifies_claims_that_should_be_validated(analyzer: CandidateAnalyzer) -> None:
    result = analyzer.analyze(CV_TEXT)
    # "Built a production RAG application" is exactly the kind of claim SPEC says must
    # not be assumed true -- it should surface as something to validate in interview.
    assert len(result.unclear_claims_to_validate) > 0
    assert any("RAG" in claim for claim in result.unclear_claims_to_validate)
    for claim in result.unclear_claims_to_validate:
        assert claim in result.important_cv_claims


def test_does_not_treat_claims_as_confirmed_facts(analyzer: CandidateAnalyzer) -> None:
    # The model itself has no field for "verified" or "confirmed" -- claims and
    # unclear_claims_to_validate are the only claim-related outputs.
    result = analyzer.analyze(CV_TEXT)
    assert not hasattr(result, "verified_claims")
    assert not hasattr(result, "confirmed_skills")


def test_extracts_experience_and_education_separately(analyzer: CandidateAnalyzer) -> None:
    result = analyzer.analyze(CV_TEXT)
    assert any("2 years" in e for e in result.experience)
    assert any("Computer Science" in e for e in result.education)


def test_rejects_blank_cv_text(analyzer: CandidateAnalyzer) -> None:
    with pytest.raises(ValueError):
        analyzer.analyze("   ")


def test_sparse_cv_yields_sparse_but_valid_output(analyzer: CandidateAnalyzer) -> None:
    result = analyzer.analyze("I like solving problems.")
    assert isinstance(result, CandidateAnalysis)
    assert result.skills == [] or "Problem Solving" in result.skills
    assert result.education == []
