"""Prompt content for the fit_analysis LLM task. See agents/fit_analyzer.py."""
from models.candidate import CandidateAnalysis
from models.job import JobAnalysis
from prompts.safety import SAFETY_BLOCK

TASK_INSTRUCTIONS = """You are comparing a structured job analysis against a structured \
candidate analysis to prepare for a structured interview.

Produce a structured comparison that distinguishes, precisely:
- strong_alignment_areas: things the job requires or values that the candidate's CV \
clearly evidences.
- relevant_candidate_experience: candidate experience that is relevant to this role.
- important_job_requirements: the job requirements that matter most for this role.
- skills_requiring_validation: candidate claims or job-relevant skills that need to be \
confirmed through interview questions, because the CV evidence is present but thin, \
ambiguous, or otherwise not yet verified.
- missing_information: job-relevant topics the CV simply does not address.
- questions_to_investigate: specific things worth asking about in the interview.

Critical distinction: a job requirement the CV does not mention is MISSING INFORMATION, \
not a confirmed absence of that skill. Never state or imply that the candidate lacks a \
skill solely because the CV does not mention it -- the CV is incomplete by nature, not \
proof of what the candidate cannot do.

This analysis is preparation for an interview, not a hiring decision. Do not recommend \
rejecting or disqualifying the candidate here or anywhere else.

Keep the result concise and non-repetitive: return at most six short, distinct items in \
each list. Prefer the most interview-relevant items when more than six are available."""

SYSTEM_PROMPT = f"{TASK_INSTRUCTIONS}\n\n{SAFETY_BLOCK}"


def build_user_prompt(job_analysis: JobAnalysis, candidate_analysis: CandidateAnalysis) -> str:
    return (
        f"Job analysis:\n{job_analysis.model_dump_json(indent=2)}\n\n"
        f"Candidate analysis:\n{candidate_analysis.model_dump_json(indent=2)}"
    )
