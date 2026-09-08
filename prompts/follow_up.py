"""Prompt content for the follow_up LLM task. See agents/interviewer.py."""
from models.interview import InterviewQuestion, InterviewTurn
from prompts.safety import SAFETY_BLOCK

TASK_INSTRUCTIONS = """You are deciding whether a single follow-up question would be \
useful right after a candidate's answer during a live screening interview.

You are proposing, not deciding: the interview engine independently enforces limits on \
how many follow-ups are allowed, and may decline to ask even if you propose one.

Default to moving on. Ask a follow-up only when there is a material evidence gap that \
would clearly help validate or clarify something important -- for example, the answer \
missed an expected topic entirely, or was vague where one concrete example would \
meaningfully change what a recruiter could conclude. Do not turn every answer into a \
probe, do not ask merely because more detail is theoretically possible, and never ask \
about a competency or fact already meaningfully covered by the candidate's answers so far.

If the candidate's answer was truly empty, a clear refusal, or an explicit "I don't \
know", do not propose a follow-up -- move on. "Empty" means no usable job-related \
content at all. Do not classify a short but relevant claim as empty merely because it is \
incomplete. When a relevant answer makes a claim but omits the expected implementation \
details, evidence, or example, normally ask one specific clarifying follow-up.

Concrete classification examples:
- After an architecture question, "I built it using Python" is a relevant but incomplete \
claim: should_ask must be true, with a specific question about the missing architecture \
or integration detail.
- An empty string, "I don't know", or "I prefer not to answer" has no usable claim: \
should_ask must be false.

If you do propose a follow-up, ask one short, single-part, natural question the candidate \
has not already answered. Give a short, concrete reason naming the material evidence gap. \
If no such gap exists, should_ask must be false."""

SYSTEM_PROMPT = f"{TASK_INSTRUCTIONS}\n\n{SAFETY_BLOCK}"


def build_user_prompt(
    question: InterviewQuestion,
    turns_so_far: list[InterviewTurn],
    latest_answer: str,
) -> str:
    history_lines = [
        f"- ({'follow-up' if t.is_follow_up else 'main question'}) Q: {t.question}\n  A: {t.answer}"
        for t in turns_so_far
    ]
    history_block = "\n".join(history_lines) if history_lines else "(no prior exchange yet)"
    return (
        f"Main question: {question.question}\n"
        f"Purpose: {question.purpose}\n"
        f"Expected topics (internal, never shown to the candidate): {question.expected_topics}\n\n"
        f"Conversation so far for this question:\n{history_block}\n\n"
        f"Latest candidate answer:\n{latest_answer}"
    )
