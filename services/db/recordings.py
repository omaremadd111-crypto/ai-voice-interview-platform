"""Repositories for interview recordings, voice transcript segments and consent.

All three are synchronous and PostgreSQL-backed, exactly like every other
repository in this package. Nothing here may be called from the realtime voice
event loop directly -- the voice layer reaches them through background tasks in
``services/livekit/v2/recording.py`` and ``transcript_recorder.py``.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models.common import RecordingStatus, TranscriptSpeaker
from models.platform import (
    InterviewConsentRecord,
    InterviewRecordingRecord,
    VoiceTranscriptSegmentRecord,
)
from services.db.orm_models import (
    InterviewConsentRow,
    InterviewRecordingRow,
    VoiceTranscriptSegmentRow,
)


# --- recordings -------------------------------------------------------------


class RecordingRepository(ABC):
    @abstractmethod
    def upsert(self, record: InterviewRecordingRecord) -> InterviewRecordingRecord: ...

    @abstractmethod
    def get_for_session(self, session_id: str) -> InterviewRecordingRecord | None: ...


class SQLAlchemyRecordingRepository(RecordingRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert(self, record: InterviewRecordingRecord) -> InterviewRecordingRecord:
        """One row per session, created or updated in place.

        Upsert rather than insert because a recording's row is written at least
        twice -- PENDING before egress is requested, then ACTIVE, then a
        terminal state -- and the session is the identity, not the attempt.
        """
        with self._session_factory() as session:
            row = session.execute(
                select(InterviewRecordingRow).where(
                    InterviewRecordingRow.interview_session_id == record.interview_session_id
                )
            ).scalar_one_or_none()

            if row is None:
                row = InterviewRecordingRow(
                    interview_session_id=record.interview_session_id
                )
                session.add(row)

            row.status = record.status.value
            row.egress_id = record.egress_id
            row.file_path = record.file_path
            row.file_url = record.file_url
            row.file_size_bytes = record.file_size_bytes
            row.duration_seconds = record.duration_seconds
            row.started_at = record.started_at
            row.ended_at = record.ended_at
            row.error = record.error
            session.commit()
            session.refresh(row)
            return _recording_to_model(row)

    def get_for_session(self, session_id: str) -> InterviewRecordingRecord | None:
        with self._session_factory() as session:
            row = session.execute(
                select(InterviewRecordingRow).where(
                    InterviewRecordingRow.interview_session_id == session_id
                )
            ).scalar_one_or_none()
            return _recording_to_model(row) if row is not None else None


def _recording_to_model(row: InterviewRecordingRow) -> InterviewRecordingRecord:
    return InterviewRecordingRecord(
        id=row.id,
        interview_session_id=row.interview_session_id,
        status=RecordingStatus(row.status),
        egress_id=row.egress_id,
        file_path=row.file_path,
        file_url=row.file_url,
        file_size_bytes=row.file_size_bytes,
        duration_seconds=row.duration_seconds,
        started_at=row.started_at,
        ended_at=row.ended_at,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# --- voice transcript segments ----------------------------------------------


class VoiceTranscriptRepository(ABC):
    @abstractmethod
    def append_many(
        self, session_id: str, segments: list[VoiceTranscriptSegmentRecord]
    ) -> int: ...

    @abstractmethod
    def list_for_session(self, session_id: str) -> list[VoiceTranscriptSegmentRecord]: ...


class SQLAlchemyVoiceTranscriptRepository(VoiceTranscriptRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append_many(
        self, session_id: str, segments: list[VoiceTranscriptSegmentRecord]
    ) -> int:
        """Append a batch of utterances.

        Append-only, unlike the question-centric transcript repository which
        replaces a session's turns wholesale. Segments arrive in batches from a
        background drain task, and a batch that partially overlaps an earlier
        one (a retry after a failed flush) must not duplicate rows -- the unique
        (session, sequence) constraint is what enforces that, so already-present
        sequences are skipped rather than re-inserted.
        """
        if not segments:
            return 0

        with self._session_factory() as session:
            existing = {
                seq
                for (seq,) in session.execute(
                    select(VoiceTranscriptSegmentRow.sequence).where(
                        VoiceTranscriptSegmentRow.interview_session_id == session_id
                    )
                ).all()
            }
            rows = [
                VoiceTranscriptSegmentRow(
                    interview_session_id=session_id,
                    sequence=segment.sequence,
                    speaker=segment.speaker.value,
                    content=segment.content,
                    spoken_at=segment.spoken_at,
                    question_id=segment.question_id,
                )
                for segment in segments
                if segment.sequence not in existing
            ]
            if not rows:
                return 0
            session.add_all(rows)
            session.commit()
            return len(rows)

    def list_for_session(self, session_id: str) -> list[VoiceTranscriptSegmentRecord]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(VoiceTranscriptSegmentRow)
                    .where(VoiceTranscriptSegmentRow.interview_session_id == session_id)
                    .order_by(VoiceTranscriptSegmentRow.sequence)
                )
                .scalars()
                .all()
            )
            return [_segment_to_model(row) for row in rows]


def _segment_to_model(row: VoiceTranscriptSegmentRow) -> VoiceTranscriptSegmentRecord:
    return VoiceTranscriptSegmentRecord(
        id=row.id,
        interview_session_id=row.interview_session_id,
        sequence=row.sequence,
        speaker=TranscriptSpeaker(row.speaker),
        content=row.content,
        spoken_at=row.spoken_at,
        question_id=row.question_id,
        created_at=row.created_at,
    )


# --- consent ----------------------------------------------------------------


class ConsentRepository(ABC):
    @abstractmethod
    def record(self, consent: InterviewConsentRecord) -> InterviewConsentRecord: ...

    @abstractmethod
    def get_for_session(self, session_id: str) -> InterviewConsentRecord | None: ...


class SQLAlchemyConsentRepository(ConsentRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(self, consent: InterviewConsentRecord) -> InterviewConsentRecord:
        """Store consent, or return what is already stored.

        Idempotent: a candidate who reloads the join page and consents again has
        not withdrawn and re-given consent. The FIRST consent timestamp is the
        one that matters legally, so it is never overwritten.
        """
        with self._session_factory() as session:
            existing = session.execute(
                select(InterviewConsentRow).where(
                    InterviewConsentRow.interview_session_id == consent.interview_session_id
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _consent_to_model(existing)

            row = InterviewConsentRow(
                interview_session_id=consent.interview_session_id,
                candidate_id=consent.candidate_id,
                consented_at=consent.consented_at or datetime.now(timezone.utc),
                consent_version=consent.consent_version,
                consent_text=consent.consent_text,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _consent_to_model(row)

    def get_for_session(self, session_id: str) -> InterviewConsentRecord | None:
        with self._session_factory() as session:
            row = session.execute(
                select(InterviewConsentRow).where(
                    InterviewConsentRow.interview_session_id == session_id
                )
            ).scalar_one_or_none()
            return _consent_to_model(row) if row is not None else None


def _consent_to_model(row: InterviewConsentRow) -> InterviewConsentRecord:
    return InterviewConsentRecord(
        id=row.id,
        interview_session_id=row.interview_session_id,
        candidate_id=row.candidate_id,
        consented_at=row.consented_at,
        consent_version=row.consent_version,
        consent_text=row.consent_text,
        created_at=row.created_at,
    )
