"""Candidate Analyzer agent: converts a CV into a structured CandidateAnalysis.

Depends only on LLMService -- no provider SDK, no Gradio.
"""
from models.candidate import CandidateAnalysis
from prompts.candidate_analysis import SYSTEM_PROMPT, build_user_prompt
from services.candidate_name import extract_explicit_candidate_name
from services.llm.base import LLMRequest, LLMService


class CandidateAnalyzer:
    def __init__(self, llm_service: LLMService) -> None:
        self._llm = llm_service

    def analyze(self, cv_text: str) -> CandidateAnalysis:
        if not cv_text or not cv_text.strip():
            raise ValueError("cv_text must not be blank")

        request = LLMRequest(
            task="candidate_analysis",
            system=SYSTEM_PROMPT,
            user=build_user_prompt(cv_text),
            context={"cv_text": cv_text},
        )
        analysis = self._llm.generate_structured(request, CandidateAnalysis)
        if analysis.full_name is None:
            explicit_name = extract_explicit_candidate_name(cv_text)
            if explicit_name is not None:
                return analysis.model_copy(update={"full_name": explicit_name}, deep=True)
        return analysis
