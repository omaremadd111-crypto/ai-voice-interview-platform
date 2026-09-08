"""SAFETY_BLOCK content, and its reuse (not duplication) across every prompt file."""
import prompts.candidate_analysis as candidate_analysis_prompt
import prompts.evaluation as evaluation_prompt
import prompts.fit_analysis as fit_analysis_prompt
import prompts.follow_up as follow_up_prompt
import prompts.interview_planning as interview_planning_prompt
import prompts.job_analysis as job_analysis_prompt
from prompts.safety import SAFETY_BLOCK

ALL_PROMPT_MODULES = [
    job_analysis_prompt,
    candidate_analysis_prompt,
    fit_analysis_prompt,
    interview_planning_prompt,
]

# Phrases that would indicate a prompt is asking the model to actually perform the
# behavior the safety block prohibits, as opposed to a prompt's own legitimate
# prohibition text (e.g. "do not recommend rejecting ..."), which is expected to
# contain these same words in a negated form.
_DECISION_REQUEST_PHRASES = (
    "recommend hiring", "should be hired", "should we hire", "decide to hire",
    "recommend rejecting", "should be rejected", "decide to reject", "disqualify the candidate",
)
_PROTECTED_ATTRIBUTE_REQUEST_PHRASES = (
    "candidate's gender", "candidate's age", "candidate's race", "candidate's religion",
    "candidate's disability", "sexual orientation of", "infer the candidate's",
    "estimate the candidate's age", "identify the candidate's gender",
)
_NEGATION_WORDS = ("not", "never", "avoid", "don't", "n't")


def _appears_as_positive_instruction(text: str, phrase: str) -> bool:
    """True if `phrase` appears in `text` without an immediately preceding negation."""
    idx = text.find(phrase)
    if idx == -1:
        return False
    preceding_window = text[max(0, idx - 20):idx]
    return not any(neg in preceding_window for neg in _NEGATION_WORDS)


# ---- SAFETY_BLOCK content ----

def test_safety_block_requires_evidence_based_evaluation() -> None:
    lower = SAFETY_BLOCK.lower()
    assert "job requirements" in lower
    assert "demonstrated experience" in lower or "evidence" in lower


def test_safety_block_prohibits_protected_attribute_inference() -> None:
    lower = SAFETY_BLOCK.lower()
    for attribute in ("gender", "age", "race", "religion", "nationality", "disability", "sexual orientation"):
        assert attribute in lower


def test_safety_block_prohibits_personality_and_emotion_inference() -> None:
    lower = SAFETY_BLOCK.lower()
    assert "personality" in lower
    assert "emotion" in lower
    assert "accent" in lower
    assert "appearance" in lower


def test_safety_block_prohibits_hiring_decisions() -> None:
    lower = SAFETY_BLOCK.lower()
    assert "hiring decision" in lower


def test_safety_block_prefers_insufficient_evidence_over_speculation() -> None:
    assert "Insufficient evidence" in SAFETY_BLOCK


def test_safety_block_treats_absence_as_unknown_not_disproof() -> None:
    lower = SAFETY_BLOCK.lower()
    assert "not proof" in lower or "does not" in lower and "lacks" in lower


# ---- reuse across prompt files (not duplication) ----

def test_safety_block_is_reused_verbatim_in_every_prompt_module() -> None:
    for module in ALL_PROMPT_MODULES:
        assert SAFETY_BLOCK in module.SYSTEM_PROMPT, f"{module.__name__} does not include SAFETY_BLOCK verbatim"


def test_safety_block_text_not_duplicated_inline_in_task_instructions() -> None:
    # The safety block's own distinctive sentence should appear exactly once per
    # SYSTEM_PROMPT (from the single import), not copy-pasted a second time.
    marker = "Never make or imply a hiring decision"
    for module in ALL_PROMPT_MODULES:
        assert module.SYSTEM_PROMPT.count(marker) == 1


# ---- prompts do not themselves request banned behavior ----

def test_task_instructions_never_request_a_hiring_decision() -> None:
    for module in ALL_PROMPT_MODULES:
        lower = module.TASK_INSTRUCTIONS.lower()
        for phrase in _DECISION_REQUEST_PHRASES:
            assert not _appears_as_positive_instruction(lower, phrase), (
                f"{module.__name__} TASK_INSTRUCTIONS positively requests: {phrase}"
            )


def test_task_instructions_never_request_protected_attribute_inference() -> None:
    for module in ALL_PROMPT_MODULES:
        lower = module.TASK_INSTRUCTIONS.lower()
        for phrase in _PROTECTED_ATTRIBUTE_REQUEST_PHRASES:
            assert not _appears_as_positive_instruction(lower, phrase), (
                f"{module.__name__} TASK_INSTRUCTIONS positively requests: {phrase}"
            )


def test_fit_analysis_instructions_explicitly_forbid_auto_rejection() -> None:
    lower = fit_analysis_prompt.TASK_INSTRUCTIONS.lower()
    assert "not a hiring decision" in lower
    assert "reject" in lower  # present as a prohibition, not a request


def test_fit_analysis_instructions_state_missing_is_not_absent() -> None:
    lower = fit_analysis_prompt.TASK_INSTRUCTIONS.lower()
    assert "missing information" in lower
    assert "not a confirmed absence" in lower or "not proof" in lower or "never state or imply" in lower


def test_fit_analysis_instructions_bound_real_provider_output() -> None:
    lower = fit_analysis_prompt.TASK_INSTRUCTIONS.lower()
    assert "concise" in lower
    assert "non-repetitive" in lower
    assert "at most six" in lower


def test_follow_up_distinguishes_incomplete_relevant_answers_from_empty_answers() -> None:
    lower = follow_up_prompt.TASK_INSTRUCTIONS.lower()
    assert "no usable job-related content" in lower
    assert "do not classify a short but relevant claim as empty" in lower
    assert "normally ask one specific clarifying follow-up" in lower
    assert '"i built it using python"' in lower
    assert "should_ask must be true" in lower
    assert "should_ask must be false" in lower


def test_evaluation_requires_verbatim_transcript_evidence() -> None:
    lower = evaluation_prompt.TASK_INSTRUCTIONS.lower()
    assert "verbatim excerpts copied exactly" in lower
    assert "do not paraphrase" in lower
    assert "never invent" in lower


def test_evaluation_defines_one_substantive_answer_as_scorable() -> None:
    lower = evaluation_prompt.TASK_INSTRUCTIONS.lower()
    assert "at least one substantive" in lower
    assert "do not require multiple examples" in lower
    assert "limited breadth" in lower and "areas_to_validate" in lower
