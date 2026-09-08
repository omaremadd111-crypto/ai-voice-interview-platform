"""Prompt content for the job_analysis LLM task. See agents/job_analyzer.py."""
from prompts.safety import SAFETY_BLOCK

TASK_INSTRUCTIONS = """You are analyzing a job description to prepare for a structured \
candidate interview. Convert the job description into a structured analysis.

Identify, using only what the job description actually states or clearly implies:
- required_skills: skills/technologies explicitly required.
- nice_to_have_skills: skills described as preferred, a plus, or nice to have.
- responsibilities: the concrete duties of the role.
- technical_topics: technical areas worth probing in the interview.
- behavioral_competencies: soft skills or competencies the role calls for.
- role_summary: a short, factual summary of the role.
- experience_level: the seniority/experience expectation, in the job description's own \
terms (free text), or null if it is not stated.

Do not invent requirements the job description does not support. If something is not \
stated, leave the corresponding list empty rather than guessing."""

SYSTEM_PROMPT = f"{TASK_INSTRUCTIONS}\n\n{SAFETY_BLOCK}"


def build_user_prompt(job_title: str, experience_level: str | None, description_text: str) -> str:
    level_line = experience_level if experience_level else "(not specified)"
    return (
        f"Job title: {job_title}\n"
        f"Stated experience level: {level_line}\n\n"
        f"Job description:\n{description_text}"
    )
