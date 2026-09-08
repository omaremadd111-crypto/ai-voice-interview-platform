"""Interviewer agent: proposes whether a follow-up question is useful right now.

Proposes only -- it does not manage interview state, increment any counters,
decide whether a follow-up budget is exhausted, score the candidate, give
feedback, or make a hiring recommendation. Those responsibilities belong to
InterviewEngine (state/limits) and, from Phase 6, the Evaluator (scoring).

Depends only on LLMService -- no provider SDK, no Gradio.
"""
from models.interview import FollowUpDecision, InterviewQuestion, InterviewTurn
from prompts.follow_up import SYSTEM_PROMPT, build_user_prompt
from services.llm.base import LLMRequest, LLMService


class Interviewer:
    def __init__(self, llm_service: LLMService) -> None:
        self._llm = llm_service

    def propose_follow_up(
        self,
        question: InterviewQuestion,
        turns_so_far: list[InterviewTurn],
        latest_answer: str,
    ) -> FollowUpDecision:
        request = LLMRequest(
            task="follow_up",
            system=SYSTEM_PROMPT,
            user=build_user_prompt(question, turns_so_far, latest_answer),
            context={
                "question": question,
                "turns_so_far": turns_so_far,
                "latest_answer": latest_answer,
            },
            temperature=0.0,
        )
        return self._llm.generate_structured(request, FollowUpDecision)
