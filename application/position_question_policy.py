"""Domain policy for reusable Position Question Bank content.

The Position bank is role/JD-only. Candidate CV claims and candidate-only
categories belong to Candidate Interview Plans and must never cross this
boundary, whether content came from an LLM or a manual API call.
"""
import re
from collections.abc import Iterable

from models.common import POSITION_BASELINE_CATEGORIES, QuestionCategory


class PositionQuestionPolicyError(ValueError):
    """Raised when content is not reusable across every applicant to a role."""


_CANDIDATE_CONTEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("your CV", re.compile(r"\byour\s+cv\b", re.IGNORECASE)),
    ("your resume", re.compile(r"\byour\s+r[ée]sum[ée]\b", re.IGNORECASE)),
    ("your application", re.compile(r"\byour\s+application\b", re.IGNORECASE)),
    ("your profile", re.compile(r"\byour\s+profile\b", re.IGNORECASE)),
    ("you mentioned", re.compile(r"\byou\s+(?:mentioned|stated|claimed)\b", re.IGNORECASE)),
    ("I saw/see you", re.compile(r"\bi\s+(?:see|saw|noticed)\s+you\b", re.IGNORECASE)),
    ("according to your", re.compile(r"\baccording\s+to\s+your\b", re.IGNORECASE)),
    (
        "candidate CV context",
        re.compile(
            r"\b(?:the|this|a|candidate(?:'s)?)\s+(?:cv|r[ée]sum[ée]|profile)\s+"
            r"(?:mentions|highlights|shows|states|lists|claims|indicates)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "named candidate address",
        re.compile(
            r"^(?:hi\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?,\s+"
            r"(?:can|could|would|tell|describe|explain|walk|how|what|why)\b"
        ),
    ),
)


def candidate_context_indicators(values: Iterable[str | None]) -> set[str]:
    """Return policy labels only; never echo potentially sensitive source text."""
    combined = "\n".join(value for value in values if value)
    return {
        label
        for label, pattern in _CANDIDATE_CONTEXT_PATTERNS
        if pattern.search(combined)
    }


def validate_position_question_category(category: QuestionCategory) -> None:
    if category not in POSITION_BASELINE_CATEGORIES:
        allowed = ", ".join(sorted(item.value for item in POSITION_BASELINE_CATEGORIES))
        raise PositionQuestionPolicyError(
            f"Category '{category.value}' is not valid for reusable Position Question Bank "
            f"questions. Allowed categories: {allowed}."
        )


def validate_position_question_content(
    *,
    category: QuestionCategory,
    question: str,
    purpose: str | None = None,
    expected_topics: Iterable[str] = (),
) -> None:
    validate_position_question_category(category)
    indicators = candidate_context_indicators((question, purpose, *expected_topics))
    if indicators:
        raise PositionQuestionPolicyError(
            "Position Question Bank content must be reusable and based only on the role/job "
            "description; candidate CV, resume, profile, claims, and names are not allowed."
        )
