"""Interview preparation request/response schemas.

The response reuses the existing domain models directly (JobAnalysis,
CandidateAnalysis, FitAnalysis, InterviewPlan) -- they are already
transport-neutral Pydantic models with no ORM coupling, so a parallel
duplicate schema would add nothing. This is the safe HR-review representation:
it contains exactly what PrepareInterviewResult already contains, never rubric
weights, evaluator prompts, or scoring internals.
"""
from pydantic import BaseModel

from models.candidate import CandidateAnalysis, FitAnalysis
from models.common import InterviewState
from models.interview import InterviewPlan
from models.job import JobAnalysis


class PrepareInterviewRequest(BaseModel):
    position_id: int
    candidate_id: int


class PrepareInterviewResponse(BaseModel):
    session_id: str
    state: InterviewState
    job_analysis: JobAnalysis
    candidate_analysis: CandidateAnalysis
    fit_analysis: FitAnalysis
    interview_plan: InterviewPlan
    llm_provider: str
    is_mock: bool
