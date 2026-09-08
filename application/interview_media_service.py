"""Read-side service for an interview's recording and speaker-labelled transcript.

Exists so the API layer depends on an application service rather than on
concrete repositories: routers validate, authorize, call a service and
serialize, and ``api/dependencies.py`` remains the one place that wires
persistence. (See tests/test_governance.py.)

Read-only. Nothing here starts, stops or mutates a recording -- that lifecycle
belongs to the voice worker, off the realtime path.
"""
from dataclasses import dataclass, field

from models.platform import InterviewRecordingRecord, VoiceTranscriptSegmentRecord
from services.db.recordings import RecordingRepository, VoiceTranscriptRepository


@dataclass(frozen=True)
class InterviewMedia:
    """What HR needs to review one interview.

    ``recording`` is None when nothing was ever recorded for the session, which
    is meaningfully different from a recording that was attempted and failed --
    the frontend distinguishes the two.
    """

    interview_session_id: str
    recording: InterviewRecordingRecord | None = None
    transcript: list[VoiceTranscriptSegmentRecord] = field(default_factory=list)


class InterviewMediaService:
    def __init__(
        self,
        recording_repo: RecordingRepository,
        transcript_repo: VoiceTranscriptRepository,
    ) -> None:
        self._recordings = recording_repo
        self._transcripts = transcript_repo

    def get_media(self, session_id: str) -> InterviewMedia:
        return InterviewMedia(
            interview_session_id=session_id,
            recording=self._recordings.get_for_session(session_id),
            transcript=self._transcripts.list_for_session(session_id),
        )
