"""Prompt content for the evaluation LLM task. See agents/evaluator.py."""
from models.common import EvaluationCategory
from models.interview import InterviewTurn
from prompts.safety import SAFETY_BLOCK

CATEGORY_GUIDANCE: dict[EvaluationCategory, str] = {
    EvaluationCategory.RELEVANT_EXPERIENCE: (
        "Use candidate-background and CV/project-validation answers for concrete "
        "responsibilities, scope, actions, and outcomes relevant to the role. Relevant "
        "academic or project experience is scorable when concrete, but its context and "
        "limitations should affect the score and areas_to_validate."
    ),
    EvaluationCategory.TECHNICAL_KNOWLEDGE: (
        "Use technical explanations from CV/project-validation and technical answers, "
        "including tools, concepts, implementation choices, trade-offs, and results."
    ),
    EvaluationCategory.PROBLEM_SOLVING: (
        "Use problem-solving answers that demonstrate diagnosis, prioritization, "
        "reasoning, trade-offs, actions, or measured outcomes."
    ),
    EvaluationCategory.COMMUNICATION: (
        "Use explicit stakeholder or team communication examples and the candidate's "
        "ability to explain relevant work clearly. One concrete explanation or stakeholder "
        "example is scorable. Do not infer personality or emotion."
    ),
    EvaluationCategory.BEHAVIORAL_COMPETENCIES: (
        "Use explicit examples of collaboration, ownership, adaptability, conflict "
        "resolution, or learning from behavioral answers. One concrete example is "
        "scorable; use areas_to_validate for competencies not covered."
    ),
    EvaluationCategory.JOB_REQUIREMENT_COVERAGE: (
        "Use CV/project-validation and technical answers that directly demonstrate a "
        "stated role requirement."
    ),
}

TASK_INSTRUCTIONS = """You are evaluating a candidate's interview answers for ONE \
specific evaluation category. You are not deciding whether to hire the candidate -- \
you are producing a structured, evidence-based assessment a human recruiter will use. \
Overall scoring and any recommendation are computed separately, outside of your output.

Base your evaluation only on the transcript excerpts provided for this category.

Sufficiency standard:
- sufficient_evidence=true when at least one substantive, category-relevant answer gives a \
concrete example, explanation, action, responsibility, decision, or outcome that can be \
quoted exactly.
- Do not require multiple examples, exhaustive coverage, a quantified outcome, or prior paid \
employment before scoring. Reflect limited breadth, context, or depth in the score and \
areas_to_validate instead.
- sufficient_evidence=false only when the relevant transcript is absent, empty, vague, or \
unrelated enough that a category score would be a guess. In that case, set score=null, \
reasoning="Insufficient evidence", and evidence=[].

A transcript turn may legitimately support more than one evaluation category. Assess \
only the aspects relevant to the requested category, even when the original interview \
question had a different label.

If you do have sufficient evidence:
- Give a score from 0 to 100 reflecting how well the evidence demonstrates this category.
- reasoning must explain the score using only what is in the transcript.
- evidence must be short VERBATIM excerpts copied exactly from a candidate answer in the \
provided transcript. Do not paraphrase, rewrite, or merge separate statements. Never invent or \
embellish evidence. Use multiple separate excerpts when necessary.
- areas_to_validate should list anything relevant that remains unclear or unconfirmed."""

SYSTEM_PROMPT = f"{TASK_INSTRUCTIONS}\n\n{SAFETY_BLOCK}"


def build_user_prompt(
    category: EvaluationCategory,
    expected_topics: list[str],
    turns: list[InterviewTurn],
) -> str:
    if turns:
        transcript_lines = [f"- Q: {t.question}\n  A: {t.answer}" for t in turns]
        transcript_block = "\n".join(transcript_lines)
    else:
        transcript_block = "(no relevant transcript turns for this category)"
    return (
        f"Evaluation category: {category.value}\n"
        f"Category-specific guidance: {CATEGORY_GUIDANCE[category]}\n"
        f"Relevant expected topics (internal context, never shown to the candidate): {expected_topics}\n\n"
        f"Relevant transcript excerpts:\n{transcript_block}"
    )
