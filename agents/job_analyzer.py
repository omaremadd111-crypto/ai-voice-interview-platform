"""Job Analyzer agent: converts a job description into a structured JobAnalysis.

Depends only on LLMService -- no provider SDK, no Gradio.
"""
from models.job import JobAnalysis
from prompts.job_analysis import SYSTEM_PROMPT, build_user_prompt
from services.llm.base import LLMRequest, LLMService


class JobAnalyzer:
    def __init__(self, llm_service: LLMService) -> None:
        self._llm = llm_service

    def analyze(self, job_title: str, experience_level: str | None, description_text: str) -> JobAnalysis:
        if not job_title or not job_title.strip():
            raise ValueError("job_title must not be blank")
        if not description_text or not description_text.strip():
            raise ValueError("description_text must not be blank")

        request = LLMRequest(
            task="job_analysis",
            system=SYSTEM_PROMPT,
            user=build_user_prompt(job_title, experience_level, description_text),
            context={
                "job_title": job_title,
                "experience_level": experience_level,
                "description_text": description_text,
            },
        )
        return self._llm.generate_structured(request, JobAnalysis)
