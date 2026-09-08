"""Prompt content for the candidate_analysis LLM task. See agents/candidate_analyzer.py."""
from prompts.safety import SAFETY_BLOCK

TASK_INSTRUCTIONS = """You are analyzing a candidate's CV to prepare for a structured \
interview. Convert the CV into a structured analysis.

Identify, using only what the CV actually states:
- full_name: the candidate's name when it is explicitly shown in the CV, otherwise null.
- skills, technologies, experience, projects, education: extracted directly from the CV.
- important_cv_claims: notable claims the candidate makes about what they did.
- relevant_experience: experience that appears relevant to a technical/professional role.
- unclear_claims_to_validate: claims that sound significant but are not yet substantiated \
with detail (scope, role, outcome, technical approach) and are worth asking about in the \
interview.

A CV claim is a claim, not a verified fact. Do not treat any claim as confirmed. For \
example, "Built a production RAG system" is a topic to validate through interview \
questions (architecture, retrieval strategy, evaluation, and so on), not a confirmed \
accomplishment.

Only extract what the CV supports. Do not infer characteristics about the candidate that \
are not explicitly relevant to evidencing job-related skills or experience. A name must \
be copied from an explicit CV heading or name field, never inferred from an email address, \
username, nationality, or any other indirect clue."""

SYSTEM_PROMPT = f"{TASK_INSTRUCTIONS}\n\n{SAFETY_BLOCK}"


def build_user_prompt(cv_text: str) -> str:
    return f"Candidate CV:\n{cv_text}"
