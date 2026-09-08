"""Evaluator agent: produces one evidence-based CategoryEvaluation per category.

Never computes an overall score or a hiring recommendation -- that belongs to
services/scoring.py, in pure Python, using config/rubrics.json. This agent
also verifies that any evidence the LLM returns is actually traceable to the
transcript, downgrading to insufficient evidence (and logging it) if not --
a real provider could plausibly return evidence that sounds plausible but was
never actually said; Pydantic alone only checks the evidence list is
non-empty, not that it is true.

Depends only on LLMService -- no provider SDK, no Gradio.
"""
from models.common import EvaluationCategory, QuestionCategory
from models.evaluation import CategoryEvaluation
from models.interview import InterviewPlan, InterviewQuestion, InterviewTranscript, InterviewTurn
from prompts.evaluation import SYSTEM_PROMPT, build_user_prompt
from services.llm.base import LLMRequest, LLMService
from services.logging_service import Event, log_event

# A substantive answer may support more than the question's nominal category. For
# example, a CV-validation answer can demonstrate relevant experience, technical
# knowledge, and role-requirement coverage, while a behavioral answer can demonstrate
# both communication and behavioral competencies. Introduction/closing remain excluded.
_EVALUATION_TO_QUESTION_CATEGORIES: dict[
    EvaluationCategory, tuple[QuestionCategory, ...]
] = {
    EvaluationCategory.RELEVANT_EXPERIENCE: (
        QuestionCategory.CANDIDATE_BACKGROUND,
        QuestionCategory.CV_PROJECT_VALIDATION,
    ),
    EvaluationCategory.TECHNICAL_KNOWLEDGE: (
        QuestionCategory.CV_PROJECT_VALIDATION,
        QuestionCategory.TECHNICAL,
    ),
    EvaluationCategory.PROBLEM_SOLVING: (
        QuestionCategory.PROBLEM_SOLVING,
    ),
    EvaluationCategory.COMMUNICATION: (
        QuestionCategory.BEHAVIORAL,
    ),
    EvaluationCategory.BEHAVIORAL_COMPETENCIES: (
        QuestionCategory.BEHAVIORAL,
    ),
    EvaluationCategory.JOB_REQUIREMENT_COVERAGE: (
        QuestionCategory.CV_PROJECT_VALIDATION,
        QuestionCategory.TECHNICAL,
    ),
}


def _questions_for_evaluation_category(
    plan: InterviewPlan, category: EvaluationCategory,
) -> list[InterviewQuestion]:
    return [
        q for q in plan.questions
        if q.category in _EVALUATION_TO_QUESTION_CATEGORIES[category]
    ]


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _is_traceable(evidence_item: str, turns: list[InterviewTurn]) -> bool:
    candidate = evidence_item[:-1] if evidence_item.endswith("…") else evidence_item
    candidate = _normalize_trace_text(candidate)
    if not candidate:
        return False
    return any(candidate in _normalize_trace_text(turn.answer) for turn in turns)


def _normalize_trace_text(value: str) -> str:
    """Normalize formatting, never meaning, for exact transcript traceability.

    Providers sometimes normalize curly quotes, dashes, or whitespace when copying a
    verbatim excerpt. Accept those presentation-only differences without accepting a
    semantic paraphrase that the candidate never said.
    """
    translation = str.maketrans({
        "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
    })
    return " ".join(value.translate(translation).strip().split()).casefold()


class Evaluator:
    def __init__(self, llm_service: LLMService) -> None:
        self._llm = llm_service

    def evaluate_category(
        self,
        category: EvaluationCategory,
        plan: InterviewPlan,
        transcript: InterviewTranscript,
        session_id: str | None = None,
    ) -> CategoryEvaluation:
        relevant_questions = _questions_for_evaluation_category(plan, category)
        relevant_question_ids = {q.id for q in relevant_questions}
        relevant_turns = [t for t in transcript.turns if t.question_id in relevant_question_ids]
        expected_topics = _dedup([topic for q in relevant_questions for topic in q.expected_topics])

        request = LLMRequest(
            task="evaluation",
            system=SYSTEM_PROMPT,
            user=build_user_prompt(category, expected_topics, relevant_turns),
            context={"category": category, "expected_topics": expected_topics, "turns": relevant_turns},
        )
        evaluation = self._llm.generate_structured(request, CategoryEvaluation)
        return self._verify_evidence(evaluation, relevant_turns, session_id)

    def evaluate_all_categories(
        self,
        plan: InterviewPlan,
        transcript: InterviewTranscript,
        session_id: str | None = None,
        categories: list[EvaluationCategory] | None = None,
    ) -> list[CategoryEvaluation]:
        cats = categories if categories is not None else list(EvaluationCategory)
        return [self.evaluate_category(c, plan, transcript, session_id) for c in cats]

    def _verify_evidence(
        self,
        evaluation: CategoryEvaluation,
        relevant_turns: list[InterviewTurn],
        session_id: str | None,
    ) -> CategoryEvaluation:
        if not evaluation.sufficient_evidence:
            return evaluation

        traceable = [e for e in evaluation.evidence if _is_traceable(e, relevant_turns)]
        untraceable = [e for e in evaluation.evidence if e not in traceable]
        if not untraceable:
            return evaluation

        log_event(
            Event.EVIDENCE_DOWNGRADED,
            session_id=session_id,
            category=evaluation.category.value,
            untraceable_count=len(untraceable),
        )
        if traceable:
            return evaluation.model_copy(update={"evidence": traceable}, deep=True)
        return CategoryEvaluation(
            category=evaluation.category,
            score=None,
            sufficient_evidence=False,
            reasoning=(
                "Insufficient evidence: the evaluator's evidence could not be verified "
                "against the interview transcript."
            ),
            evidence=[],
            areas_to_validate=evaluation.areas_to_validate,
        )
