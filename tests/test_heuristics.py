"""Unit tests for the deterministic mock heuristics: term extraction, coverage,
specificity, breadth, and evidence picking."""
from models.common import QuestionCategory
from models.interview import InterviewTurn
from services.llm.mock import heuristics as h


# ---- extract_terms ----

def test_extract_terms_finds_known_lexicon_terms() -> None:
    text = "I used Python and a vector database with RAG for retrieval."
    terms = h.extract_terms(text)
    assert "python" in terms
    assert "rag" in terms
    assert "vector_database" in terms
    assert "retrieval" in terms


def test_extract_terms_is_sorted_and_deduplicated() -> None:
    text = "Python python PYTHON Python"
    terms = h.extract_terms(text)
    assert terms == ["python"]


def test_extract_terms_respects_word_boundaries() -> None:
    # "ml" should not match inside "html" or "small"
    text = "I wrote html and made a small change."
    terms = h.extract_terms(text)
    assert "machine_learning" not in terms


def test_extract_terms_empty_text_returns_empty_list() -> None:
    assert h.extract_terms("") == []


# ---- coverage ----

def test_coverage_full_when_all_topics_present() -> None:
    text = "We discussed retrieval strategy and vector database choices in depth."
    score = h.coverage(text, ["retrieval strategy", "vector database"])
    assert score == 1.0


def test_coverage_partial_when_some_topics_missing() -> None:
    text = "We discussed retrieval strategy only."
    score = h.coverage(text, ["retrieval strategy", "vector database"])
    assert score == 0.5


def test_coverage_zero_when_no_topics_present() -> None:
    text = "This answer is about something completely unrelated."
    score = h.coverage(text, ["retrieval strategy", "vector database"])
    assert score == 0.0


def test_coverage_defaults_to_full_when_no_topics_expected() -> None:
    assert h.coverage("anything", []) == 1.0


def test_coverage_matches_via_lexicon_synonym() -> None:
    # "vector db" is a lexicon alias for the same canonical term as "vector database"
    text = "I used a vector db for semantic search."
    assert h.topic_mentioned(text, "vector database") is True


# ---- specificity ----

def test_specificity_low_for_short_vague_answer() -> None:
    vague = "It was good."
    detailed = (
        "I designed the retrieval strategy using hybrid search with a vector database "
        "because keyword-only search missed 30% of relevant documents. I evaluated it "
        "against a 200-query ground truth set and improved recall by 18%."
    )
    assert h.specificity(vague) < h.specificity(detailed)


def test_specificity_empty_answer_is_zero() -> None:
    assert h.specificity("") == 0.0
    assert h.specificity("   ") == 0.0


def test_specificity_bounded_between_zero_and_one() -> None:
    long_detailed = (
        "I built and implemented and designed and optimized and deployed a system with "
        "python sql docker aws rag retrieval vector database because it was needed so that "
        "throughput improved by 42% and latency dropped by 100ms, therefore the team adopted it."
    )
    score = h.specificity(long_detailed)
    assert 0.0 <= score <= 1.0


def test_specificity_rewards_numbers_and_causal_language() -> None:
    without_numbers = "I improved the system because it was slow so that users were happier."
    with_numbers = without_numbers + " Latency dropped by 42%."
    assert h.specificity(with_numbers) > h.specificity(without_numbers)


# ---- breadth ----

def test_breadth_increases_with_distinct_terms() -> None:
    narrow = "I used Python."
    broad = "I used Python, SQL, Docker, RAG, and a vector database."
    assert h.breadth(broad) > h.breadth(narrow)


def test_breadth_bounded_at_one() -> None:
    text = "python java javascript sql nosql docker kubernetes aws azure gcp git github"
    assert h.breadth(text) == 1.0


# ---- pick_evidence: traceability, never invents ----

def _turn(answer: str, qid: str = "q1") -> InterviewTurn:
    return InterviewTurn(
        question_id=qid, question="Q", category=QuestionCategory.TECHNICAL,
        answer=answer, is_follow_up=False, timestamp="2026-08-19T00:00:00Z",
    )


def test_pick_evidence_returns_substrings_of_actual_answers() -> None:
    turns = [_turn(
        "I built a RAG pipeline using Python and a vector database. "
        "I evaluated it against a ground truth set and improved recall by 18%."
    )]
    evidence = h.pick_evidence(turns)
    assert len(evidence) > 0
    full_text = turns[0].answer
    for item in evidence:
        # Evidence is either a verbatim sentence or that sentence trimmed with an ellipsis.
        stripped = item[:-1] if item.endswith("…") else item
        assert stripped in full_text


def test_pick_evidence_returns_empty_list_for_empty_transcript() -> None:
    assert h.pick_evidence([]) == []


def test_pick_evidence_returns_empty_list_when_answers_are_trivial() -> None:
    turns = [_turn("Yes."), _turn("Sure.", qid="q2")]
    assert h.pick_evidence(turns) == []


def test_pick_evidence_respects_limit() -> None:
    turns = [_turn(
        "I built a RAG pipeline with Python because latency mattered. "
        "I evaluated retrieval quality against a ground truth set. "
        "I deployed it to production using Docker and AWS. "
        "I mentored two junior engineers on the vector database design."
    )]
    evidence = h.pick_evidence(turns, limit=2)
    assert len(evidence) <= 2


def test_pick_evidence_is_deterministic_across_calls() -> None:
    turns = [_turn(
        "I built a RAG pipeline with Python. I evaluated retrieval quality carefully. "
        "I deployed it to production using Docker."
    )]
    first = h.pick_evidence(turns)
    second = h.pick_evidence(turns)
    assert first == second


def test_pick_evidence_truncates_long_sentences_with_ellipsis() -> None:
    long_sentence = "I " + "built a very long and detailed retrieval pipeline " * 5 + "successfully."
    turns = [_turn(long_sentence)]
    evidence = h.pick_evidence(turns)
    assert len(evidence) == 1
    assert len(evidence[0]) <= h.MAX_EVIDENCE_LENGTH
    assert evidence[0].endswith("…")


# ---- split_sentences ----

def test_split_sentences_splits_on_terminal_punctuation() -> None:
    sentences = h.split_sentences("First sentence. Second sentence! Third one?")
    assert sentences == ["First sentence.", "Second sentence!", "Third one?"]


def test_split_sentences_empty_text() -> None:
    assert h.split_sentences("") == []
    assert h.split_sentences("   ") == []
