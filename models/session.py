"""Interview session aggregate: the unit of state isolation between candidates."""
from pydantic import BaseModel, Field

from models.candidate import CandidateAnalysis, CandidateInput, FitAnalysis
from models.common import InterviewState
from models.evaluation import HRReport, InterviewEvaluation
from models.interview import InterviewPlan, InterviewTranscript
from models.job import JobAnalysis, JobInput


class InterviewSession(BaseModel):
    id: str
    company: str
    # Platform references are optional so the standalone/Gradio flow stays
    # transport-neutral, while queue-created sessions remain queryable from the
    # owning candidate after evaluation.
    position_id: int | None = None
    candidate_id: int | None = None
    job_input: JobInput | None = None
    job_analysis: JobAnalysis | None = None
    candidate_input: CandidateInput | None = None
    candidate_analysis: CandidateAnalysis | None = None
    fit_analysis: FitAnalysis | None = None
    interview_plan: InterviewPlan | None = None
    state: InterviewState = InterviewState.CREATED
    transcript: InterviewTranscript = Field(default_factory=InterviewTranscript)
    current_question_index: int = 0
    current_follow_up_count: int = 0
    total_follow_up_count: int = 0
    pending_question_text: str | None = None
    evaluation: InterviewEvaluation | None = None
    report: HRReport | None = None
    created_at: str
    updated_at: str
    llm_provider: str
    is_mock: bool
    # Optimistic concurrency token: every SessionStore rejects a save() whose version
    # does not match the currently stored version, so a stale write fails instead of
    # silently overwriting newer state.
    version: int = 0
