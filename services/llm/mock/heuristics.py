"""Deterministic text-analysis primitives for Mock Mode.

No randomness, no time-dependence: identical input always yields identical
output. These are the building blocks MockLLMService uses instead of calling
a real model -- lexicon-driven term extraction, topic coverage, answer
specificity, and evidence picking that only ever returns text that actually
appears in the transcript.
"""
import re

from models.interview import InterviewTurn
from services.llm.mock.lexicon import LEXICON

_STOPWORDS = {
    "the", "and", "for", "with", "your", "you", "how", "what", "did", "was",
    "were", "are", "of", "to", "in", "on", "a", "an", "is", "it", "that", "this",
}
_CAUSAL_CONNECTIVES = ("because", "so that", "in order to", "as a result", "which allowed", "therefore")
_ACTION_VERB_PATTERN = re.compile(
    r"\bi\s+(built|implemented|designed|optimi[sz]ed|led|wrote|debugged|deployed|created|"
    r"configured|integrated|migrated|automated|reduced|improved|refactored|tested)\b",
    re.IGNORECASE,
)
_NUMBER_PATTERN = re.compile(r"\d")
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")
MAX_EVIDENCE_LENGTH = 160


def extract_terms(text: str) -> list[str]:
    """Sorted canonical lexicon terms whose alias appears as a whole word/phrase in text."""
    if not text:
        return []
    found: set[str] = set()
    for canonical, aliases in LEXICON.items():
        for alias in aliases:
            if re.search(r"\b" + re.escape(alias) + r"\b", text, re.IGNORECASE):
                found.add(canonical)
                break
    return sorted(found)


def _significant_words(phrase: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", phrase.lower())
    return [w for w in words if len(w) >= 3 and w not in _STOPWORDS]


def _topic_to_canonical(topic: str) -> str | None:
    for canonical, aliases in LEXICON.items():
        if topic == canonical.replace("_", " "):
            return canonical
        if topic in aliases:
            return canonical
    return None


def topic_mentioned(text: str, topic: str) -> bool:
    """Whether a free-text expected-topic string is meaningfully present in text."""
    normalized_topic = topic.strip().lower()
    if not normalized_topic or not text:
        return False
    if re.search(r"\b" + re.escape(normalized_topic) + r"\b", text, re.IGNORECASE):
        return True
    canonical = _topic_to_canonical(normalized_topic)
    if canonical and canonical in extract_terms(text):
        return True
    sig_words = _significant_words(normalized_topic)
    if not sig_words:
        return False
    matches = sum(1 for w in sig_words if re.search(r"\b" + re.escape(w) + r"\b", text, re.IGNORECASE))
    return matches >= max(1, (len(sig_words) + 1) // 2)


def coverage(text: str, expected_topics: list[str]) -> float:
    """Fraction of expected_topics meaningfully present in text; 1.0 if none were expected."""
    if not expected_topics:
        return 1.0
    mentioned = sum(1 for topic in expected_topics if topic_mentioned(text, topic))
    return mentioned / len(expected_topics)


def breadth(text: str, cap: int = 5) -> float:
    """How many distinct lexicon concepts appear in text, relative to `cap`."""
    return min(len(extract_terms(text)) / cap, 1.0)


def specificity(answer: str) -> float:
    """0..1 from length, numeric/metric mentions, distinct technical terms, causal
    language, and concrete first-person action verbs."""
    answer = answer.strip()
    if not answer:
        return 0.0
    word_count = len(answer.split())
    length_score = min(word_count / 40, 1.0)
    number_score = 1.0 if _NUMBER_PATTERN.search(answer) else 0.0
    term_score = min(len(extract_terms(answer)) / 3, 1.0)
    causal_score = 1.0 if any(c in answer.lower() for c in _CAUSAL_CONNECTIVES) else 0.0
    verb_score = 1.0 if _ACTION_VERB_PATTERN.search(answer) else 0.0
    weighted = (
        0.30 * length_score + 0.20 * number_score + 0.25 * term_score
        + 0.10 * causal_score + 0.15 * verb_score
    )
    return max(0.0, min(1.0, weighted))


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT_PATTERN.split(text) if s.strip()]


def trim_to_length(sentence: str, max_len: int = MAX_EVIDENCE_LENGTH) -> str:
    if len(sentence) <= max_len:
        return sentence
    return sentence[: max_len - 1].rstrip() + "…"


def pick_evidence(turns: list[InterviewTurn], limit: int = 3) -> list[str]:
    """Up to `limit` real, transcript-traceable sentences, ranked by specificity.

    Never fabricates text: every result is a trimmed substring of an actual
    candidate answer, so evidence can always be traced back to the transcript.
    """
    seen: set[str] = set()
    candidates: list[tuple[float, str]] = []
    for turn in turns:
        for sentence in split_sentences(turn.answer):
            if len(sentence) < 15:
                continue
            trimmed = trim_to_length(sentence)
            if trimmed in seen:
                continue
            seen.add(trimmed)
            candidates.append((specificity(sentence), trimmed))
    candidates.sort(key=lambda pair: (-pair[0], pair[1]))
    return [sentence for _, sentence in candidates[:limit]]
