"""JobAnalyzer agent tests, run against MockLLMService (no API key required)."""
import pytest

from agents.job_analyzer import JobAnalyzer
from models.job import JobAnalysis
from services.llm.mock.lexicon import LEXICON
from services.llm.mock.mock_service import MockLLMService, _display_label

# Fictional demo content for testing only -- not an actual FlairsTech vacancy.
JD_TEXT = (
    "FlairsTech is hiring a Junior AI Engineer to help build and maintain retrieval "
    "augmented generation (RAG) systems for internal tooling. Responsibilities include "
    "designing vector database pipelines, writing Python and SQL, and collaborating with "
    "the platform team on system design. Nice to have: experience with Docker and AWS."
)


@pytest.fixture
def analyzer() -> JobAnalyzer:
    return JobAnalyzer(MockLLMService())


def test_returns_valid_job_analysis(analyzer: JobAnalyzer) -> None:
    result = analyzer.analyze("Junior AI Engineer", "Junior", JD_TEXT)
    assert isinstance(result, JobAnalysis)
    assert result.job_title == "Junior AI Engineer"
    assert result.experience_level == "Junior"


def test_extracts_job_relevant_technical_topics(analyzer: JobAnalyzer) -> None:
    result = analyzer.analyze("Junior AI Engineer", "Junior", JD_TEXT)
    assert "Python" in result.technical_topics
    assert "RAG" in result.technical_topics
    assert "Vector Database" in result.technical_topics


def test_separates_required_from_nice_to_have(analyzer: JobAnalyzer) -> None:
    result = analyzer.analyze("Junior AI Engineer", "Junior", JD_TEXT)
    assert "Python" in result.required_skills
    assert "Python" not in result.nice_to_have_skills
    # Docker/AWS are stated after "Nice to have:" in JD_TEXT.
    assert "Containers" in result.nice_to_have_skills or "Cloud" in result.nice_to_have_skills


def test_does_not_invent_unsupported_requirements(analyzer: JobAnalyzer) -> None:
    narrow_jd = "We need someone who knows Python."
    result = analyzer.analyze("Backend Engineer", None, narrow_jd)
    # Nothing about Kubernetes, cloud, or ML was ever mentioned.
    assert "Kubernetes" not in result.required_skills
    assert "Containers" not in result.required_skills
    assert "Machine Learning" not in result.required_skills
    assert "Cloud" not in result.technical_topics
    for skill in result.required_skills:
        assert skill.lower() in narrow_jd.lower() or skill == "Python"


def test_required_skills_are_traceable_to_the_jd_text(analyzer: JobAnalyzer) -> None:
    # A skill's *display label* need not be a literal JD substring (e.g. "Cloud" is
    # correctly inferred from the JD saying "AWS"), but every skill must trace back to
    # some real lexicon alias that actually appears in the JD text -- nothing invented.
    result = analyzer.analyze("Junior AI Engineer", "Junior", JD_TEXT)
    jd_lower = JD_TEXT.lower()
    label_to_canonical = {_display_label(canonical): canonical for canonical in LEXICON}
    for skill in result.required_skills + result.technical_topics:
        canonical = label_to_canonical.get(skill)
        assert canonical is not None, f"{skill!r} is not a known lexicon term"
        aliases = LEXICON[canonical]
        assert any(alias in jd_lower for alias in aliases), f"{skill!r} not traceable to any JD alias"


def test_responsibilities_are_verbatim_sentences_from_jd(analyzer: JobAnalyzer) -> None:
    result = analyzer.analyze("Junior AI Engineer", "Junior", JD_TEXT)
    for sentence in result.responsibilities:
        assert sentence in JD_TEXT


def test_rejects_blank_job_title(analyzer: JobAnalyzer) -> None:
    with pytest.raises(ValueError):
        analyzer.analyze("   ", "Junior", JD_TEXT)


def test_rejects_blank_description(analyzer: JobAnalyzer) -> None:
    with pytest.raises(ValueError):
        analyzer.analyze("Junior AI Engineer", "Junior", "   ")


def test_accepts_none_experience_level(analyzer: JobAnalyzer) -> None:
    result = analyzer.analyze("Junior AI Engineer", None, JD_TEXT)
    assert result.experience_level is None
