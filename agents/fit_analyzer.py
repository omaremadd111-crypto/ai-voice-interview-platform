"""Fit Analyzer agent: compares JobAnalysis and CandidateAnalysis to prepare for
interview planning. Never used to auto-reject a candidate.

Depends only on LLMService -- no provider SDK, no Gradio.
"""
from models.candidate import CandidateAnalysis, FitAnalysis
from models.job import JobAnalysis
from prompts.fit_analysis import SYSTEM_PROMPT, build_user_prompt
from services.llm.base import LLMRequest, LLMService


class FitAnalyzer:
    def __init__(self, llm_service: LLMService) -> None:
        self._llm = llm_service

    def analyze(self, job_analysis: JobAnalysis, candidate_analysis: CandidateAnalysis) -> FitAnalysis:
        request = LLMRequest(
            task="fit_analysis",
            system=SYSTEM_PROMPT,
            user=build_user_prompt(job_analysis, candidate_analysis),
            context={"job_analysis": job_analysis, "candidate_analysis": candidate_analysis},
        )
        return self._llm.generate_structured(request, FitAnalysis)
