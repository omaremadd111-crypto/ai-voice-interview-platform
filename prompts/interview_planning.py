"""Prompt content for the interview_planning LLM task. See agents/interview_planner.py."""
from models.candidate import CandidateAnalysis, FitAnalysis
from models.job import JobAnalysis
from prompts.safety import SAFETY_BLOCK

TASK_INSTRUCTIONS = """You are building a structured interview plan for an initial \
screening interview, to be reviewed and approved by a human recruiter before it is used.

Build a set of MAIN interview questions. Follow-up questions are handled separately \
during the interview itself and are not part of this count. Distribute questions across \
relevant categories (introduction, candidate background, CV/project validation, \
technical, problem solving, behavioral, closing) as appropriate for this specific role \
and candidate -- not a fixed generic template.

SPECIFICITY IS THE PRIORITY. This plan is for ONE named candidate, not for the role in \
general. A question that could be asked of any applicant is a wasted question whenever \
the candidate's own CV gives you something concrete to ask about.

Prioritize, in roughly this order:
1. Specific, checkable claims from the CV -- named projects, technologies the candidate \
says they used, and above all measurable results ("reduced latency by 35%", "scaled to \
2M requests/day"). Quote or paraphrase the claim so the candidate knows exactly what you \
are asking about, then ask HOW they achieved it.
2. Gaps: requirements in the job description that the CV does NOT evidence. Ask these \
openly and neutrally -- the CV being silent about a skill is not proof the candidate \
lacks it, so never phrase the question as an accusation or assume the answer.
3. Areas of strong candidate/job alignment worth probing for depth.
4. Core job requirements not already covered above.
5. Problem-solving / situational questions tied to this role's actual responsibilities.
6. Behavioral questions tied to the competencies this role needs.

COVERAGE: unless the requested question count is too small to allow it, include at least \
one technical question, one problem-solving question, and one behavioral question, so the \
plan is not lopsided toward any single dimension.

NO DUPLICATES: do not ask about the same claim, project, or skill twice, and do not \
restate a question that already appears in the position's baseline question bank you were \
given. Do not test the same competency through differently worded questions. If two \
candidate questions would overlap, keep the more specific one.

ONE CLEAR QUESTION AT A TIME: keep each main question short and conversational. Never \
bundle multiple requests into one question. Ask for one fact, example, decision, or result \
now; the adaptive interviewer can ask one focused follow-up later if important evidence is \
still missing. Avoid long preambles and checklist-style lists of sub-questions.

USE THE CANDIDATE'S NAME naturally, the way a real interviewer would -- in the opening, \
and occasionally when changing topic. Do not begin every question with their name; that \
reads as robotic. Use their first name only.

Every question must have a clear interview purpose grounded in the job analysis, \
candidate analysis, or fit analysis you were given -- never ask about a topic merely \
because a keyword appeared somewhere. Match question difficulty to the candidate's \
experience level. Questions must read like something a real interviewer would say out \
loud, not a templated placeholder.

expected_topics on each question is internal context for the interviewer/evaluator -- it \
is never shown to the candidate."""

CANDIDATE_SYSTEM_PROMPT = f"{TASK_INSTRUCTIONS}\n\n{SAFETY_BLOCK}"

POSITION_TASK_INSTRUCTIONS = """You are proposing reusable baseline screening questions for a \
Position Question Bank. These questions are for the ROLE and may be asked consistently of every \
applicant. Use only the supplied position title, company, experience level, and job analysis \
derived from the Job Description.

Never use or invent candidate-specific context. Do not mention a candidate name, CV, resume, \
application, profile, personal claim, prior statement, or wording such as "you mentioned" or \
"I see you led". Do not generate CV/project-validation questions.

Every question category must be one of: candidate_background, technical, problem_solving, or \
behavioral. Do not generate introduction or closing questions; the interview engine supplies \
those. Keep each question short, conversational, role-relevant, and reusable. Ask one clear \
question at a time. Tie technical and situational questions to actual responsibilities or \
requirements in the job analysis. Avoid duplicate competencies and duplicate wording.

expected_topics is internal context for follow-up coverage and evaluation; it is never shown to \
the applicant. Return exactly the requested number when the role context supports it."""

POSITION_SYSTEM_PROMPT = f"{POSITION_TASK_INSTRUCTIONS}\n\n{SAFETY_BLOCK}"

# Backwards-compatible name for the candidate-specific planning workflow.
SYSTEM_PROMPT = CANDIDATE_SYSTEM_PROMPT


def build_position_user_prompt(
    job_title: str,
    company_name: str,
    experience_level: str | None,
    num_questions: int,
    job_analysis: JobAnalysis,
    existing_questions: list[str] | None = None,
) -> str:
    level_line = experience_level if experience_level else "(not specified)"
    bank_block = ""
    if existing_questions:
        listed = "\n".join(f"- {question}" for question in existing_questions)
        bank_block = (
            "\n\nExisting Position Question Bank questions (do not restate them):\n"
            f"{listed}"
        )
    return (
        f"Company: {company_name}\n"
        f"Job title: {job_title}\n"
        f"Role experience level: {level_line}\n"
        f"Number of reusable baseline questions requested: {num_questions}\n\n"
        f"Job analysis derived only from the Position / Job Description:\n"
        f"{job_analysis.model_dump_json(indent=2)}"
        f"{bank_block}"
    )


def build_user_prompt(
    job_title: str,
    company_name: str,
    experience_level: str | None,
    num_questions: int,
    job_analysis: JobAnalysis,
    candidate_analysis: CandidateAnalysis,
    fit_analysis: FitAnalysis,
    existing_questions: list[str] | None = None,
) -> str:
    level_line = experience_level if experience_level else "(not specified)"
    candidate_name = candidate_analysis.full_name or "(name not provided)"

    bank_block = ""
    if existing_questions:
        listed = "\n".join(f"- {question}" for question in existing_questions)
        bank_block = (
            "\n\nPosition question bank (already asked of every applicant for this role -- "
            "do NOT restate these; your questions must add candidate-specific coverage on "
            f"top of them):\n{listed}"
        )

    return (
        f"Company: {company_name}\n"
        f"Job title: {job_title}\n"
        f"Candidate name: {candidate_name}\n"
        f"Candidate experience level: {level_line}\n"
        f"Number of main questions requested: {num_questions}\n\n"
        f"Job analysis:\n{job_analysis.model_dump_json(indent=2)}\n\n"
        f"Candidate analysis:\n{candidate_analysis.model_dump_json(indent=2)}\n\n"
        f"Fit analysis:\n{fit_analysis.model_dump_json(indent=2)}"
        f"{bank_block}"
    )
