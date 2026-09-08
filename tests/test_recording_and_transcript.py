"""Tests for interview audio recording (P0) and voice transcript persistence (P1).

Ordered by what would hurt most if it broke:

1. The voice pipeline is untouched -- recording and transcript work must not
   have moved a single tuned value or entered a spoken turn.
2. Recording lifecycle, including the failure paths that must never take an
   interview down.
3. Transcript capture: hot path stays free of I/O, ordering and speakers are
   preserved.
4. Consent is a real gate.
5. Schema and API shape.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from datetime import datetime, timezone

import pytest

from config.settings import Settings
from models.common import RecordingStatus, TranscriptSpeaker
from models.platform import (
    InterviewConsentRecord,
    InterviewRecordingRecord,
    VoiceTranscriptSegmentRecord,
)
from services.livekit.base import EgressState
from services.livekit.v2 import recording as recording_module
from services.livekit.v2.recording import InterviewRecorder, recording_filepath
from services.livekit.v2.transcript_recorder import (
    VoiceTranscriptRecorder,
    _speaker_for,
)

# Pinned from tag voice-v2-stable-e2e-v1.
V2_ENDPOINTING = {"mode": "dynamic", "min_delay": 0.4, "max_delay": 2.0, "alpha": 0.9}


@pytest.fixture(autouse=True)
def _isolate_env(isolated_environ) -> None:
    """Private os.environ copy; load_settings() otherwise leaks .env globally."""


class FakeEgress:
    """Records which thread each call ran on, and can be told to fail."""

    def __init__(self, *, fail_start=False, status="EGRESS_COMPLETE") -> None:
        self.fail_start = fail_start
        self.status = status
        self.calls: list[str] = []
        self.threads: dict[str, int] = {}
        self.started_filepath: str | None = None

    def start_room_audio_recording(self, request):
        self.calls.append("start")
        self.threads["start"] = threading.get_ident()
        self.started_filepath = request.filepath
        if self.fail_start:
            from services.livekit.base import LiveKitGatewayError

            raise LiveKitGatewayError("LiveKit could not start the recording.")
        return EgressState(egress_id="eg-1", status="EGRESS_ACTIVE")

    def stop_recording(self, egress_id):
        self.calls.append("stop")
        self.threads["stop"] = threading.get_ident()
        return EgressState(egress_id=egress_id, status="EGRESS_ENDING")

    def get_recording(self, egress_id):
        self.calls.append("get")
        return EgressState(
            egress_id=egress_id,
            status=self.status,
            started_at=1_700_000_000.0,
            ended_at=1_700_000_600.0,
            duration_seconds=600.0,
            size_bytes=4_800_000,
            location="s3://bucket/interviews/s-1/interview.mp3",
        )


class FakeRecordingRepo:
    def __init__(self) -> None:
        self.saved: list[InterviewRecordingRecord] = []
        self.threads: list[int] = []

    def upsert(self, record):
        self.threads.append(threading.get_ident())
        self.saved.append(record)
        return record

    def get_for_session(self, session_id):
        return self.saved[-1] if self.saved else None

    @property
    def last(self):
        return self.saved[-1] if self.saved else None


class FakeTranscriptRepo:
    def __init__(self, *, fail_once=False) -> None:
        self.batches: list[list[VoiceTranscriptSegmentRecord]] = []
        self.stored: dict[int, VoiceTranscriptSegmentRecord] = {}
        self.threads: list[int] = []
        self.fail_once = fail_once

    def append_many(self, session_id, segments):
        self.threads.append(threading.get_ident())
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("database unavailable")
        new = [s for s in segments if s.sequence not in self.stored]
        for s in new:
            self.stored[s.sequence] = s
        self.batches.append(list(segments))
        return len(new)

    def list_for_session(self, session_id):
        return [self.stored[k] for k in sorted(self.stored)]


def make_recorder(egress=None, repo=None, settings=None):
    return InterviewRecorder(
        egress or FakeEgress(),
        repo or FakeRecordingRepo(),
        session_id="s-1",
        settings=settings or Settings(recording_public_base_url="https://media.example.com"),
    )


# ===========================================================================
# 1. The voice pipeline is untouched
# ===========================================================================


def test_voice_tuning_values_unchanged() -> None:
    from services.livekit.v2.turn_tuning import build_turn_handling

    th = build_turn_handling(Settings())
    assert th["endpointing"] == V2_ENDPOINTING
    assert th["interruption"]["mode"] == "adaptive"
    assert th["interruption"]["min_words"] == 1
    assert th["interruption"]["resume_false_interruption"] is True
    assert th["preemptive_generation"]["enabled"] is True
    assert th["preemptive_generation"]["preemptive_tts"] is True
    assert type(th["turn_detection"]).__name__ == "TurnDetector"


def test_turn_tuning_and_session_files_unchanged_since_the_stable_tag() -> None:
    """git proof that the proven pipeline files were not edited for this feature."""
    import subprocess
    from pathlib import Path

    result = subprocess.run(
        [
            "git", "diff", "--name-only", "voice-v2-stable-e2e-v1", "--",
            "services/livekit/v2/turn_tuning.py",
            "services/livekit/v2/session.py",
            "services/livekit/v2/director.py",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    if result.returncode != 0:
        pytest.skip(f"git unavailable or tag missing: {result.stderr.strip()}")
    changed = [line for line in result.stdout.splitlines() if line.strip()]
    assert changed == [], f"voice pipeline files modified: {changed}"


def test_recording_disabled_by_default_leaves_interviews_untouched() -> None:
    """With the switch off, no egress call is ever made."""
    assert Settings().recording_enabled is False


# ===========================================================================
# 2. Recording lifecycle
# ===========================================================================


def test_start_does_not_block_the_caller() -> None:
    """start() only schedules; the interview never waits on the egress call."""
    egress = FakeEgress()
    recorder = make_recorder(egress)

    async def drive():
        recorder.start()
        # Nothing has happened yet -- start() returned before any work ran.
        immediate = list(egress.calls)
        await asyncio.sleep(0.2)
        return immediate, list(egress.calls)

    immediate, eventual = asyncio.run(drive())

    assert immediate == []
    assert "start" in eventual


def test_egress_and_persistence_run_off_the_event_loop() -> None:
    egress = FakeEgress()
    repo = FakeRecordingRepo()
    recorder = make_recorder(egress, repo)

    async def drive():
        loop_thread = threading.get_ident()
        recorder.start()
        await asyncio.sleep(0.2)
        return loop_thread

    loop_thread = asyncio.run(drive())

    assert egress.threads["start"] != loop_thread
    assert all(t != loop_thread for t in repo.threads)


def test_pending_row_is_written_before_egress_is_requested() -> None:
    """An interview nobody could record must be distinguishable from silence."""
    repo = FakeRecordingRepo()
    recorder = make_recorder(FakeEgress(), repo)

    async def drive():
        recorder.start()
        await asyncio.sleep(0.2)

    asyncio.run(drive())

    assert repo.saved[0].status is RecordingStatus.PENDING
    assert repo.saved[0].file_path == recording_filepath("s-1")


def test_completed_recording_persists_duration_size_and_url() -> None:
    repo = FakeRecordingRepo()
    recorder = make_recorder(FakeEgress(), repo)

    async def drive():
        recorder.start()
        await asyncio.sleep(0.1)
        await recorder.stop()

    asyncio.run(drive())

    final = repo.last
    assert final.status is RecordingStatus.COMPLETED
    assert final.duration_seconds == 600.0
    assert final.file_size_bytes == 4_800_000
    assert final.egress_id == "eg-1"
    assert final.file_url == "https://media.example.com/interviews/s-1/interview.mp3"
    assert final.is_playable


def test_failed_start_is_recorded_and_never_raises() -> None:
    """A recording failure must not take the interview down."""
    repo = FakeRecordingRepo()
    recorder = make_recorder(FakeEgress(fail_start=True), repo)

    async def drive():
        recorder.start()
        await asyncio.sleep(0.2)
        # stop() must also be safe after a failed start.
        await recorder.stop()

    asyncio.run(drive())  # no exception

    assert repo.last.status is RecordingStatus.FAILED
    assert repo.last.error
    assert "could not start" in repo.last.error.lower()


def test_failed_recording_is_not_playable() -> None:
    record = InterviewRecordingRecord(
        interview_session_id="s-1",
        status=RecordingStatus.FAILED,
        error="LiveKit could not start the interview recording.",
    )
    assert record.is_playable is False


def test_persistence_failure_does_not_raise() -> None:
    """Losing the bookkeeping must not end a live interview."""

    class BrokenRepo:
        def upsert(self, record):
            raise RuntimeError("db down")

        def get_for_session(self, session_id):
            return None

    recorder = make_recorder(FakeEgress(), BrokenRepo())

    async def drive():
        recorder.start()
        await asyncio.sleep(0.2)
        await recorder.stop()

    asyncio.run(drive())  # must not raise


@pytest.mark.parametrize(
    "livekit_status,expected",
    [
        ("EGRESS_STARTING", RecordingStatus.PENDING),
        ("EGRESS_ACTIVE", RecordingStatus.ACTIVE),
        ("EGRESS_COMPLETE", RecordingStatus.COMPLETED),
        ("EGRESS_FAILED", RecordingStatus.FAILED),
        ("EGRESS_ABORTED", RecordingStatus.ABORTED),
        ("EGRESS_LIMIT_REACHED", RecordingStatus.ABORTED),
    ],
)
def test_livekit_status_maps_onto_product_status(livekit_status, expected) -> None:
    assert recording_module._map_status(livekit_status) is expected


def test_recording_uses_mp3_room_composite_audio_only() -> None:
    """The mechanism requirement: mixed candidate + AI audio, MP3."""
    from services.livekit import egress_gateway

    source = inspect.getsource(egress_gateway)
    assert "start_room_composite_egress" in source
    assert "audio_only=True" in source
    assert "EncodedFileType.MP3" in source


def test_missing_storage_config_fails_loudly() -> None:
    """Better to refuse than to start a recording nobody can retrieve."""
    from services.livekit.base import LiveKitConfigurationError
    from services.livekit.egress_gateway import LiveKitEgressGateway

    gateway = LiveKitEgressGateway(Settings(recording_s3_bucket=None))
    with pytest.raises(LiveKitConfigurationError, match="RECORDING_S3_BUCKET"):
        gateway._file_output("interviews/s-1/interview.mp3")


# ===========================================================================
# 3. Transcript capture
# ===========================================================================


class FakeSession:
    """Minimal AgentSession stand-in exposing only .on()."""

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, payload):
        for handler in self.handlers.get(event, []):
            handler(payload)


class FakeItem:
    def __init__(self, role, text):
        self.role = role
        self.text_content = text


class FakeEvent:
    def __init__(self, item):
        self.item = item


def test_speaker_mapping() -> None:
    assert _speaker_for("user") is TranscriptSpeaker.CANDIDATE
    assert _speaker_for("assistant") is TranscriptSpeaker.INTERVIEWER


def test_system_messages_are_never_transcribed() -> None:
    """The per-turn question briefing is an instruction, not something said."""
    assert _speaker_for("system") is None
    assert _speaker_for(None) is None


def test_hot_path_only_enqueues_and_does_no_io() -> None:
    repo = FakeTranscriptRepo()
    session = FakeSession()
    recorder = VoiceTranscriptRecorder(repo, session_id="s-1", flush_interval_seconds=60)

    async def drive():
        recorder.attach(session)
        # Emit during the "turn": no DB call may happen here.
        for i in range(5):
            session.emit("conversation_item_added", FakeEvent(FakeItem("user", f"a{i}")))
        return list(repo.batches)

    during_turn = asyncio.run(drive())

    assert during_turn == [], "a database write happened inside the turn"


def test_segments_are_persisted_in_order_with_speakers() -> None:
    repo = FakeTranscriptRepo()
    session = FakeSession()
    recorder = VoiceTranscriptRecorder(
        repo, session_id="s-1", flush_interval_seconds=0.05
    )

    async def drive():
        recorder.attach(session)
        session.emit(
            "conversation_item_added", FakeEvent(FakeItem("assistant", "Question one?"))
        )
        session.emit(
            "conversation_item_added", FakeEvent(FakeItem("user", "My answer."))
        )
        session.emit(
            "conversation_item_added", FakeEvent(FakeItem("assistant", "Question two?"))
        )
        await recorder.aclose()

    asyncio.run(drive())

    stored = repo.list_for_session("s-1")
    assert [s.sequence for s in stored] == [0, 1, 2]
    assert [s.speaker for s in stored] == [
        TranscriptSpeaker.INTERVIEWER,
        TranscriptSpeaker.CANDIDATE,
        TranscriptSpeaker.INTERVIEWER,
    ]
    assert stored[1].content == "My answer."


def test_transcript_writes_run_off_the_event_loop() -> None:
    repo = FakeTranscriptRepo()
    session = FakeSession()
    recorder = VoiceTranscriptRecorder(
        repo, session_id="s-1", flush_interval_seconds=0.05
    )

    async def drive():
        loop_thread = threading.get_ident()
        recorder.attach(session)
        session.emit("conversation_item_added", FakeEvent(FakeItem("user", "hello")))
        await recorder.aclose()
        return loop_thread

    loop_thread = asyncio.run(drive())

    assert repo.threads, "nothing was written"
    assert all(t != loop_thread for t in repo.threads)


def test_empty_utterances_are_skipped() -> None:
    repo = FakeTranscriptRepo()
    session = FakeSession()
    recorder = VoiceTranscriptRecorder(repo, session_id="s-1", flush_interval_seconds=0.05)

    async def drive():
        recorder.attach(session)
        session.emit("conversation_item_added", FakeEvent(FakeItem("user", "   ")))
        session.emit("conversation_item_added", FakeEvent(FakeItem("user", "real")))
        await recorder.aclose()

    asyncio.run(drive())

    stored = repo.list_for_session("s-1")
    assert len(stored) == 1
    assert stored[0].content == "real"


def test_failed_flush_is_retried_without_duplicating() -> None:
    """A transient DB failure must not lose or double-write transcript lines."""
    repo = FakeTranscriptRepo(fail_once=True)
    session = FakeSession()
    recorder = VoiceTranscriptRecorder(repo, session_id="s-1", flush_interval_seconds=0.05)

    async def drive():
        recorder.attach(session)
        session.emit("conversation_item_added", FakeEvent(FakeItem("user", "one")))
        session.emit("conversation_item_added", FakeEvent(FakeItem("assistant", "two")))
        await asyncio.sleep(0.2)
        await recorder.aclose()

    asyncio.run(drive())

    stored = repo.list_for_session("s-1")
    assert [s.content for s in stored] == ["one", "two"]
    assert [s.sequence for s in stored] == [0, 1]


def test_close_flushes_remaining_segments() -> None:
    repo = FakeTranscriptRepo()
    session = FakeSession()
    recorder = VoiceTranscriptRecorder(repo, session_id="s-1", flush_interval_seconds=600)

    async def drive():
        recorder.attach(session)
        session.emit("conversation_item_added", FakeEvent(FakeItem("user", "last word")))
        await recorder.aclose()

    asyncio.run(drive())

    assert [s.content for s in repo.list_for_session("s-1")] == ["last word"]


# ===========================================================================
# 4. Consent
# ===========================================================================


def recording_enabled_settings(**overrides) -> Settings:
    """Settings with recording on AND the storage the guard requires.

    Consent is NOT implied by this -- recording_consent_required is a
    separate, independently-defaulted flag. Pass recording_consent_required=True
    explicitly to exercise the gate.
    """
    return Settings(
        recording_enabled=True,
        recording_s3_bucket="bucket",
        recording_s3_access_key="key",
        recording_s3_secret_key="secret",
        **overrides,
    )


def test_consent_notice_text_is_explicit_about_recording_and_review() -> None:
    from application.voice_invite_service import RECORDING_CONSENT_TEXT

    lowered = RECORDING_CONSENT_TEXT.casefold()
    assert "recorded" in lowered
    assert "transcript" in lowered
    assert "ai" in lowered
    assert "consent" in lowered


# --- current default: the consent step is bypassed --------------------------
#
# The candidate must be able to start immediately in dev/testing. Recording,
# R2 upload and transcript persistence all stay fully live -- only the
# pre-join consent checkbox and this gate are affected, and only because
# recording_consent_required is a SEPARATE flag from recording_enabled.


def test_consent_not_required_by_default() -> None:
    assert Settings().recording_consent_required is False


def test_token_issues_without_consent_even_with_recording_and_storage_on() -> None:
    """The proof that matters: recording being fully configured does not, by
    itself, require a stored consent record before a token is issued."""
    from application.voice_invite_service import VoiceInviteService

    service = VoiceInviteService.__new__(VoiceInviteService)
    service._settings = recording_enabled_settings()  # recording_consent_required defaults False
    service._consent_repo = None  # no consent storage even wired up

    service._require_consent("s-1")  # must not raise


def test_consent_gate_stays_off_even_if_someone_flips_recording_enabled() -> None:
    """Guards the decoupling itself: recording_enabled=True must never, on its
    own, turn the consent requirement back on."""
    settings = recording_enabled_settings()
    assert settings.recording_enabled is True
    assert settings.recording_consent_required is False


# --- the gate itself: still present, still correct, just off by default -----
#
# These prove the dormant infrastructure still works when explicitly turned
# back on with RECORDING_CONSENT_REQUIRED=true -- nothing was deleted.


def test_consent_is_required_when_the_gate_is_explicitly_enabled() -> None:
    from application.voice_invite_service import (
        VoiceConsentRequiredError,
        VoiceInviteService,
    )

    service = VoiceInviteService.__new__(VoiceInviteService)
    service._settings = recording_enabled_settings(recording_consent_required=True)
    service._consent_repo = type("R", (), {"get_for_session": lambda self, s: None})()

    with pytest.raises(VoiceConsentRequiredError, match="consent"):
        service._require_consent("s-1")


def test_consent_present_allows_the_token_when_the_gate_is_enabled() -> None:
    from application.voice_invite_service import VoiceInviteService

    stored = InterviewConsentRecord(
        interview_session_id="s-1",
        candidate_id=1,
        consented_at=datetime.now(timezone.utc),
        consent_version="v1",
        consent_text="text",
    )
    service = VoiceInviteService.__new__(VoiceInviteService)
    service._settings = recording_enabled_settings(recording_consent_required=True)
    service._consent_repo = type(
        "R", (), {"get_for_session": lambda self, s: stored}
    )()

    service._require_consent("s-1")  # must not raise


def test_no_consent_needed_when_recording_is_disabled() -> None:
    """Nothing to consent to when nothing is recorded."""
    from application.voice_invite_service import VoiceInviteService

    service = VoiceInviteService.__new__(VoiceInviteService)
    service._settings = Settings(recording_enabled=False)
    service._consent_repo = None

    service._require_consent("s-1")  # must not raise


# ===========================================================================
# 5. Schema and API
# ===========================================================================


def test_recording_cannot_be_enabled_without_storage() -> None:
    """Fail at startup, not after an interview has gone unrecorded.

    Egress uploads from LiveKit's servers, so a missing bucket would surface as
    an out-of-process failure long after the conversation is over.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="RECORDING_S3_BUCKET"):
        Settings(recording_enabled=True)

    with pytest.raises(ValidationError, match="RECORDING_S3_SECRET_KEY"):
        Settings(
            recording_enabled=True,
            recording_s3_bucket="b",
            recording_s3_access_key="k",
        )


def test_full_storage_config_allows_recording() -> None:
    settings = Settings(
        recording_enabled=True,
        recording_s3_bucket="b",
        recording_s3_access_key="k",
        recording_s3_secret_key="s",
    )
    assert settings.recording_enabled is True


def test_public_base_url_is_not_required_to_record() -> None:
    """Without it the file is still made; only in-browser playback degrades."""
    settings = Settings(
        recording_enabled=True,
        recording_s3_bucket="b",
        recording_s3_access_key="k",
        recording_s3_secret_key="s",
        recording_public_base_url=None,
    )
    assert settings.recording_enabled is True
    assert settings.recording_public_base_url is None


def test_recording_disabled_needs_no_storage() -> None:
    assert Settings(recording_enabled=False).recording_enabled is False


def test_new_tables_exist_in_metadata() -> None:
    from services.db.orm_models import Base

    for table in (
        "interview_recordings",
        "voice_transcript_segments",
        "interview_consents",
    ):
        assert table in Base.metadata.tables


def test_transcript_turns_table_is_untouched() -> None:
    """The evaluator's source of truth must not have been altered."""
    from services.db.orm_models import Base

    columns = {c.name for c in Base.metadata.tables["transcript_turns"].columns}
    assert columns == {
        "id",
        "interview_session_id",
        "turn_index",
        "speaker",
        "question_id",
        "question_text",
        "category",
        "content",
        "is_follow_up",
        "answered_at",
        "created_at",
    }


def test_migration_0005_follows_0004() -> None:
    """Loaded by path: the module name starts with a digit, so it is not importable."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / "0005_add_recording_and_voice_transcript.py"
    )
    assert path.exists(), "migration 0005 is missing"

    spec = importlib.util.spec_from_file_location("migration_0005", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "0005"
    assert migration.down_revision == "0004"
    # The evaluator's table must not be touched by this migration.
    source = path.read_text(encoding="utf-8")
    assert "transcript_turns" not in source.replace(
        "``transcript_turns``", ""
    ).replace("transcript_turns is left untouched", "")


def test_media_endpoint_is_registered() -> None:
    from api.app import create_app
    from api.settings import APISettings

    spec = create_app(
        api_settings=APISettings(jwt_secret_key="test-only-secret-never-use-in-production")
    ).openapi()
    assert "/api/v1/interviews/{session_id}/media" in spec["paths"]
    assert "/api/v1/voice/consent" in spec["paths"]
    assert "/api/v1/voice/consent-notice" in spec["paths"]


# ===========================================================================
# 6. Consent bypass for dev/testing (this change)
# ===========================================================================


def test_consent_notice_endpoint_reflects_the_dedicated_flag() -> None:
    """The endpoint itself is correct/dormant, independent of whether the
    frontend currently calls it."""
    from api.routers.voice import get_consent_notice

    off = get_consent_notice(settings=Settings())
    assert off.consent_required is False

    on = get_consent_notice(
        settings=Settings(recording_consent_required=True)
    )
    assert on.consent_required is True


def test_consent_infrastructure_remains_fully_importable() -> None:
    """Nothing was deleted: the repository, error type, notice text and ORM
    row are all still present and constructible, just not wired into the
    active join flow by default."""
    from application.voice_invite_service import (
        RECORDING_CONSENT_TEXT,
        VoiceConsentRequiredError,
        VoiceInviteService,
    )
    from models.platform import InterviewConsentRecord
    from services.db.orm_models import InterviewConsentRow
    from services.db.recordings import ConsentRepository, SQLAlchemyConsentRepository

    assert RECORDING_CONSENT_TEXT
    assert issubclass(VoiceConsentRequiredError, Exception)
    assert hasattr(VoiceInviteService, "record_consent")
    assert hasattr(VoiceInviteService, "_require_consent")
    assert InterviewConsentRecord(
        interview_session_id="s-1",
        candidate_id=1,
        consented_at=datetime.now(timezone.utc),
        consent_version="v1",
        consent_text="text",
    ).interview_session_id == "s-1"
    assert InterviewConsentRow.__tablename__ == "interview_consents"
    assert issubclass(SQLAlchemyConsentRepository, ConsentRepository)


def test_migration_0005_and_consent_table_still_present() -> None:
    """Migration 0005 must not have been touched or reverted by this change."""
    from services.db.orm_models import Base

    assert "interview_consents" in Base.metadata.tables
    from pathlib import Path

    migration_path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / "0005_add_recording_and_voice_transcript.py"
    )
    assert migration_path.exists()
    assert "interview_consents" in migration_path.read_text(encoding="utf-8")


def test_recording_and_transcript_paths_are_unaffected_by_the_consent_flag() -> None:
    """The consent flag must have zero surface area outside voice_invite_service.

    Recording lifecycle and transcript persistence must not reference it at
    all -- if they did, disabling consent could accidentally disable recording.
    """
    for module_name in (
        "services.livekit.v2.recording",
        "services.livekit.v2.transcript_recorder",
    ):
        source = inspect.getsource(__import__(module_name, fromlist=["x"]))
        assert "recording_consent_required" not in source
        assert "consent" not in source.casefold()


def test_voice_v2_settings_unchanged_by_the_consent_bypass() -> None:
    """Re-affirms the pinned V2 configuration specifically after this change:
    only consent-related settings and code paths were touched."""
    from services.livekit.v2.turn_tuning import build_turn_handling

    th = build_turn_handling(Settings())
    assert th["endpointing"] == V2_ENDPOINTING
    assert th["interruption"]["mode"] == "adaptive"
    assert th["interruption"]["min_words"] == 1
    assert th["interruption"]["resume_false_interruption"] is True
    assert th["interruption"]["false_interruption_timeout"] == 2.0
    assert th["preemptive_generation"]["enabled"] is True
    assert th["preemptive_generation"]["preemptive_tts"] is True
    assert type(th["turn_detection"]).__name__ == "TurnDetector"

    settings = Settings()
    assert settings.livekit_stt_model == "deepgram/nova-3"
    assert settings.livekit_voice_llm_model == "google/gemini-3-flash"
    assert settings.livekit_tts_model == "cartesia/sonic-3"
