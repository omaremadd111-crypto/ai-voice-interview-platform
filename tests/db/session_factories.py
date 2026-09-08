"""Shared InterviewSession builders for tests/db/. Not a test module itself."""
from models.candidate import CandidateInput
from models.common import InterviewState, QuestionCategory
from models.interview import InterviewPlan, InterviewQuestion
from models.session import InterviewSession

NOW = "2026-08-19T00:00:00+00:00"


def minimal_session(session_id: str, **overrides: object) -> InterviewSession:
    """A minimal, valid InterviewSession -- enough to satisfy interview_sessions'
    NOT NULL columns and the transcript_turns/evaluations foreign key."""
    defaults: dict[str, object] = dict(
        id=session_id,
        company="Acme",
        candidate_input=CandidateInput(full_name="Jordan Rivera", cv_text="Built a Python project."),
        interview_plan=InterviewPlan(questions=[
            InterviewQuestion(
                id="q1",
                category=QuestionCategory.TECHNICAL,
                question="Explain your Python project.",
                purpose="Assess technical depth.",
                expected_topics=["Python"],
                difficulty="medium",
            ),
        ]),
        state=InterviewState.CREATED,
        created_at=NOW,
        updated_at=NOW,
        llm_provider="mock",
        is_mock=True,
    )
    defaults.update(overrides)
    return InterviewSession(**defaults)
