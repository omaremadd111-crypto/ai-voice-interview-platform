"""Interview Planner agent with explicit candidate-plan and position-bank scopes.

Both scopes build the same structured InterviewPlan through the same LLM task,
while their method signatures and prompts keep candidate context out of reusable
Position Question Bank generation.

Depends only on LLMService -- no provider SDK, no Gradio. Does not manage
interview state; that is the InterviewEngine's job (Phase 5).
"""
from models.candidate import CandidateAnalysis, FitAnalysis
from models.interview import InterviewPlan
from models.job import JobAnalysis
from prompts.interview_planning import (
    CANDIDATE_SYSTEM_PROMPT,
    POSITION_SYSTEM_PROMPT,
    build_position_user_prompt,
    build_user_prompt,
)
from services.llm.base import LLMRequest, LLMService

MIN_QUESTIONS = 3
MAX_QUESTIONS = 15


class InterviewPlanner:
    def __init__(self, llm_service: LLMService) -> None:
        self._llm = llm_service

    def plan(
        self,
        job_title: str,
        company_name: str,
        experience_level: str | None,
        num_questions: int,
        job_analysis: JobAnalysis,
        candidate_analysis: CandidateAnalysis,
        fit_analysis: FitAnalysis,
        existing_questions: list[str] | None = None,
    ) -> InterviewPlan:
        """existing_questions is the position's baseline question bank, when there is
        one. It is passed as context so the generated candidate-specific questions
        add coverage on top of the baseline instead of restating it."""
        if not MIN_QUESTIONS <= num_questions <= MAX_QUESTIONS:
            raise ValueError(
                f"num_questions must be between {MIN_QUESTIONS} and {MAX_QUESTIONS}, got {num_questions}"
            )

        request = LLMRequest(
            task="interview_planning",
            system=CANDIDATE_SYSTEM_PROMPT,
            user=build_user_prompt(
                job_title, company_name, experience_level, num_questions,
                job_analysis, candidate_analysis, fit_analysis, existing_questions,
            ),
            context={
                "scope": "candidate_plan",
                "job_title": job_title,
                "company_name": company_name,
                "experience_level": experience_level,
                "num_questions": num_questions,
                "job_analysis": job_analysis,
                "candidate_analysis": candidate_analysis,
                "fit_analysis": fit_analysis,
                "existing_questions": existing_questions or [],
            },
        )
        return self._llm.generate_structured(request, InterviewPlan)

    def plan_position_questions(
        self,
        job_title: str,
        company_name: str,
        experience_level: str | None,
        num_questions: int,
        job_analysis: JobAnalysis,
        existing_questions: list[str] | None = None,
    ) -> InterviewPlan:
        """Generate reusable role/JD-only questions through the same planner task.

        Candidate context is intentionally absent from this signature, prompt,
        and LLM context. Passing candidate_id/CV/analysis therefore fails at the
        Python boundary instead of being silently ignored.
        """
        if not MIN_QUESTIONS <= num_questions <= MAX_QUESTIONS:
            raise ValueError(
                f"num_questions must be between {MIN_QUESTIONS} and {MAX_QUESTIONS}, "
                f"got {num_questions}"
            )
        request = LLMRequest(
            task="interview_planning",
            system=POSITION_SYSTEM_PROMPT,
            user=build_position_user_prompt(
                job_title,
                company_name,
                experience_level,
                num_questions,
                job_analysis,
                existing_questions,
            ),
            context={
                "scope": "position_baseline",
                "job_title": job_title,
                "company_name": company_name,
                "experience_level": experience_level,
                "num_questions": num_questions,
                "job_analysis": job_analysis,
                "existing_questions": existing_questions or [],
            },
        )
        return self._llm.generate_structured(request, InterviewPlan)
