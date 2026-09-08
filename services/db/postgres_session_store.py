"""PostgreSQL-backed SessionStore implementation.

Persists InterviewSession across three tables in one transaction per create()/
save() call:
  - interview_sessions: scalar/queryable columns + a session_data JSONB blob for
    the write-once analysis fields (job/candidate analysis, fit analysis, plan).
  - transcript_turns: one row per InterviewTurn, replaced wholesale on every save
    (matches how InterviewTranscript.turns is built up as a plain list).
  - evaluations: one row, upserted once the interview is evaluated/reported.

Optimistic concurrency: save() performs a single `UPDATE ... WHERE id = :id AND
version = :expected_version` and checks whether it affected a row. That check-and-
increment happens atomically in one SQL statement, so two concurrent writers can
never both believe they won -- the loser's UPDATE affects zero rows and the whole
transaction (including any transcript/evaluation writes already staged in it)
rolls back, raising StaleSessionVersionError instead of silently overwriting
newer state.
"""
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from models.candidate import CandidateAnalysis, CandidateInput, FitAnalysis
from models.common import InterviewState, QuestionCategory, RecommendationLevel, ScreeningOutcome, TranscriptSpeaker
from models.evaluation import CategoryEvaluation, HRReport, InterviewEvaluation
from models.interview import InterviewPlan, InterviewTranscript, InterviewTurn
from models.job import JobAnalysis, JobInput
from models.session import InterviewSession
from services.db.orm_models import EvaluationRow, InterviewSessionRow, TranscriptTurnRow
from services.session_service import (
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SessionStore,
    StaleSessionVersionError,
    validate_session_id,
)


class PostgresSessionStore(SessionStore):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, session: InterviewSession) -> None:
        session_id = validate_session_id(session.id)
        with self._session_factory() as db:
            if db.get(InterviewSessionRow, session_id) is not None:
                raise SessionAlreadyExistsError(f"Session '{session_id}' already exists")
            row = InterviewSessionRow(
                id=session_id,
                position_id=session.position_id,
                candidate_id=session.candidate_id,
                company=session.company,
                state=session.state.value,
                current_question_index=session.current_question_index,
                current_follow_up_count=session.current_follow_up_count,
                total_follow_up_count=session.total_follow_up_count,
                pending_question_text=session.pending_question_text,
                llm_provider=session.llm_provider,
                is_mock=session.is_mock,
                session_data=_session_data_blob(session),
                created_at=session.created_at,
                updated_at=session.updated_at,
                version=session.version,
            )
            db.add(row)
            db.flush()
            _replace_transcript_rows(db, session_id, session.transcript.turns)
            _upsert_evaluation_row(db, session_id, session.evaluation, session.report)
            db.commit()

    def get(self, session_id: str) -> InterviewSession:
        normalized_id = validate_session_id(session_id)
        with self._session_factory() as db:
            row = db.get(InterviewSessionRow, normalized_id)
            if row is None:
                raise SessionNotFoundError(f"Session '{normalized_id}' was not found")

            turn_rows = db.scalars(
                select(TranscriptTurnRow)
                .where(TranscriptTurnRow.interview_session_id == normalized_id)
                .order_by(TranscriptTurnRow.turn_index)
            ).all()
            turns = [_turn_from_row(r) for r in turn_rows]

            eval_row = db.scalar(select(EvaluationRow).where(EvaluationRow.interview_session_id == normalized_id))
            evaluation = _evaluation_from_row(eval_row) if eval_row is not None else None
            report = (
                HRReport.model_validate(eval_row.report_data)
                if eval_row is not None and eval_row.report_data is not None
                else None
            )

            return _session_from_row(row, turns, evaluation, report)

    def save(self, session: InterviewSession) -> None:
        session_id = validate_session_id(session.id)
        with self._session_factory() as db:
            stmt = (
                update(InterviewSessionRow)
                .where(InterviewSessionRow.id == session_id, InterviewSessionRow.version == session.version)
                .values(
                    company=session.company,
                    position_id=session.position_id,
                    candidate_id=session.candidate_id,
                    state=session.state.value,
                    current_question_index=session.current_question_index,
                    current_follow_up_count=session.current_follow_up_count,
                    total_follow_up_count=session.total_follow_up_count,
                    pending_question_text=session.pending_question_text,
                    llm_provider=session.llm_provider,
                    is_mock=session.is_mock,
                    session_data=_session_data_blob(session),
                    updated_at=session.updated_at,
                    version=InterviewSessionRow.version + 1,
                )
                .returning(InterviewSessionRow.version)
            )
            updated = db.execute(stmt).first()
            if updated is None:
                exists = db.get(InterviewSessionRow, session_id) is not None
                db.rollback()
                if not exists:
                    raise SessionNotFoundError(f"Session '{session_id}' was not found")
                raise StaleSessionVersionError(
                    f"Session '{session_id}' was modified since it was last read "
                    f"(expected version {session.version})"
                )
            new_version = updated[0]

            _replace_transcript_rows(db, session_id, session.transcript.turns)
            _upsert_evaluation_row(db, session_id, session.evaluation, session.report)
            db.commit()
            session.version = new_version


def _session_data_blob(session: InterviewSession) -> dict:
    return {
        "job_input": session.job_input.model_dump(mode="json") if session.job_input is not None else None,
        "job_analysis": session.job_analysis.model_dump(mode="json") if session.job_analysis is not None else None,
        "candidate_input": (
            session.candidate_input.model_dump(mode="json") if session.candidate_input is not None else None
        ),
        "candidate_analysis": (
            session.candidate_analysis.model_dump(mode="json") if session.candidate_analysis is not None else None
        ),
        "fit_analysis": session.fit_analysis.model_dump(mode="json") if session.fit_analysis is not None else None,
        "interview_plan": (
            session.interview_plan.model_dump(mode="json") if session.interview_plan is not None else None
        ),
    }


def _replace_transcript_rows(db: Session, session_id: str, turns: list[InterviewTurn]) -> None:
    db.execute(delete(TranscriptTurnRow).where(TranscriptTurnRow.interview_session_id == session_id))
    for index, turn in enumerate(turns):
        db.add(TranscriptTurnRow(
            interview_session_id=session_id,
            turn_index=index,
            # InterviewTurn only ever records the candidate's answer -- see
            # TranscriptSpeaker's docstring in models/common.py.
            speaker=TranscriptSpeaker.CANDIDATE.value,
            question_id=turn.question_id,
            question_text=turn.question,
            category=turn.category.value,
            content=turn.answer,
            is_follow_up=turn.is_follow_up,
            answered_at=turn.timestamp,
        ))


def _upsert_evaluation_row(
    db: Session, session_id: str, evaluation: InterviewEvaluation | None, report: HRReport | None,
) -> None:
    if evaluation is None:
        return
    row = db.scalar(select(EvaluationRow).where(EvaluationRow.interview_session_id == session_id))
    if row is None:
        row = EvaluationRow(interview_session_id=session_id)
        db.add(row)
    row.overall_score = evaluation.overall_score
    row.evidence_coverage = evaluation.evidence_coverage
    row.recommendation = evaluation.recommendation.value
    row.screening_outcome = evaluation.screening_outcome.value
    row.rubric_profile = evaluation.rubric_profile
    row.category_results = [ce.model_dump(mode="json") for ce in evaluation.category_evaluations]
    if report is not None:
        row.strengths = report.strengths
        row.validation_areas = report.areas_requiring_validation
        row.summary = report.interview_summary
        row.report_data = report.model_dump(mode="json")


def _turn_from_row(row: TranscriptTurnRow) -> InterviewTurn:
    return InterviewTurn(
        question_id=row.question_id,
        question=row.question_text,
        category=QuestionCategory(row.category),
        answer=row.content,
        is_follow_up=row.is_follow_up,
        timestamp=row.answered_at,
    )


def _evaluation_from_row(row: EvaluationRow) -> InterviewEvaluation:
    return InterviewEvaluation(
        category_evaluations=[CategoryEvaluation.model_validate(c) for c in row.category_results],
        overall_score=row.overall_score,
        evidence_coverage=row.evidence_coverage,
        recommendation=RecommendationLevel(row.recommendation),
        screening_outcome=ScreeningOutcome(row.screening_outcome),
        rubric_profile=row.rubric_profile,
    )


def _session_from_row(
    row: InterviewSessionRow, turns: list[InterviewTurn], evaluation: InterviewEvaluation | None,
    report: HRReport | None,
) -> InterviewSession:
    data = row.session_data
    return InterviewSession(
        id=row.id,
        company=row.company,
        position_id=row.position_id,
        candidate_id=row.candidate_id,
        job_input=JobInput.model_validate(data["job_input"]) if data.get("job_input") else None,
        job_analysis=JobAnalysis.model_validate(data["job_analysis"]) if data.get("job_analysis") else None,
        candidate_input=(
            CandidateInput.model_validate(data["candidate_input"]) if data.get("candidate_input") else None
        ),
        candidate_analysis=(
            CandidateAnalysis.model_validate(data["candidate_analysis"]) if data.get("candidate_analysis") else None
        ),
        fit_analysis=FitAnalysis.model_validate(data["fit_analysis"]) if data.get("fit_analysis") else None,
        interview_plan=InterviewPlan.model_validate(data["interview_plan"]) if data.get("interview_plan") else None,
        state=InterviewState(row.state),
        transcript=InterviewTranscript(turns=turns),
        current_question_index=row.current_question_index,
        current_follow_up_count=row.current_follow_up_count,
        total_follow_up_count=row.total_follow_up_count,
        pending_question_text=row.pending_question_text,
        evaluation=evaluation,
        report=report,
        created_at=row.created_at,
        updated_at=row.updated_at,
        llm_provider=row.llm_provider,
        is_mock=row.is_mock,
        version=row.version,
    )
