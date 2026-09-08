"""Interview audio recording, driven entirely off the realtime event loop.

Everything expensive here -- the egress API round trip, the status poll, the
database write -- is synchronous or network-bound, so none of it is ever
awaited inside a spoken turn. ``start()`` and ``stop()`` return immediately
after scheduling a background task; the voice pipeline never observes them.

Failure policy: a recording that cannot start, or dies mid-interview, must
never take the interview down with it. Every path here swallows its exception,
records it as a terminal FAILED state with a safe message, and lets the
conversation continue. An interview without audio is still a valid interview --
the transcript and the evaluation do not depend on it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import Settings
from models.common import RecordingStatus
from models.platform import InterviewRecordingRecord
from services.db.recordings import RecordingRepository
from services.livekit.base import (
    EgressStartRequest,
    EgressState,
    LiveKitConfigurationError,
    LiveKitEgressPort,
    LiveKitGatewayError,
    room_name_for_session,
)

logger = logging.getLogger("interview_agent.livekit.v2.recording")

# How long to keep polling for the finalized file after stopping. Egress
# finishes uploading a moment after it stops, so the duration and size are not
# available immediately.
_FINALIZE_POLL_ATTEMPTS = 10
_FINALIZE_POLL_INTERVAL_SECONDS = 2.0

# LiveKit egress status -> product status.
_STATUS_MAP = {
    "EGRESS_STARTING": RecordingStatus.PENDING,
    "EGRESS_ACTIVE": RecordingStatus.ACTIVE,
    "EGRESS_ENDING": RecordingStatus.ACTIVE,
    "EGRESS_COMPLETE": RecordingStatus.COMPLETED,
    "EGRESS_FAILED": RecordingStatus.FAILED,
    "EGRESS_ABORTED": RecordingStatus.ABORTED,
    "EGRESS_LIMIT_REACHED": RecordingStatus.ABORTED,
}


def recording_filepath(session_id: str) -> str:
    """Storage key for one interview's audio. Stable and non-PII."""
    return f"interviews/{session_id}/interview.mp3"


def _map_status(livekit_status: str) -> RecordingStatus:
    return _STATUS_MAP.get(livekit_status, RecordingStatus.PENDING)


def _utc(epoch_seconds: float | None) -> datetime | None:
    if not epoch_seconds:
        return None
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)


class InterviewRecorder:
    """Owns the recording lifecycle for one interview session."""

    def __init__(
        self,
        egress: LiveKitEgressPort,
        repository: RecordingRepository,
        *,
        session_id: str,
        settings: Settings,
    ) -> None:
        self._egress = egress
        self._repository = repository
        self._session_id = session_id
        self._settings = settings
        self._egress_id: str | None = None
        self._tasks: set[asyncio.Task] = set()
        self._started = asyncio.Event()

    # --- non-blocking entry points ------------------------------------------

    def start(self) -> None:
        """Schedule the recording start. Returns immediately.

        Called from the session handler before audio begins. Nothing about the
        conversation waits on the egress round trip; if it is slow or fails, the
        interview has already started talking.
        """
        self._spawn(self._start_recording(), "start")

    def stop(self) -> asyncio.Task:
        """Schedule stop and finalization.

        Returns the task so teardown can optionally await it *after* the room is
        finished -- never during a turn.
        """
        return self._spawn(self._stop_recording(), "stop")

    def _spawn(self, coro, label: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=f"recording-{label}-{self._session_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- background work -----------------------------------------------------

    async def _start_recording(self) -> None:
        # Write PENDING first so an interview nobody could record is
        # distinguishable from one where recording was never attempted.
        await self._persist(
            InterviewRecordingRecord(
                interview_session_id=self._session_id,
                status=RecordingStatus.PENDING,
                file_path=recording_filepath(self._session_id),
                started_at=datetime.now(timezone.utc),
            )
        )

        try:
            state = await asyncio.to_thread(
                self._egress.start_room_audio_recording,
                EgressStartRequest(
                    room_name=room_name_for_session(self._session_id),
                    filepath=recording_filepath(self._session_id),
                ),
            )
        except (LiveKitGatewayError, LiveKitConfigurationError) as exc:
            await self._fail(str(exc))
            return
        except Exception as exc:  # never take the interview down
            logger.exception("recording_start_unexpected session=%s", self._session_id)
            await self._fail(f"Recording could not start ({type(exc).__name__}).")
            return

        self._egress_id = state.egress_id or None
        self._started.set()
        logger.info(
            "recording started session=%s egress=%s", self._session_id, self._egress_id
        )
        await self._persist_state(state, default=RecordingStatus.ACTIVE)

    async def _stop_recording(self) -> None:
        # The start task may still be in flight if the interview was very short.
        try:
            await asyncio.wait_for(self._started.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(
                "recording never started before stop session=%s", self._session_id
            )
            return

        if not self._egress_id:
            return

        try:
            await asyncio.to_thread(self._egress.stop_recording, self._egress_id)
        except (LiveKitGatewayError, LiveKitConfigurationError) as exc:
            # An egress that already ended on its own is not a failure worth
            # overwriting a good recording for; finalize below decides.
            logger.warning(
                "recording stop failed session=%s: %s", self._session_id, exc
            )
        except Exception:
            logger.exception("recording_stop_unexpected session=%s", self._session_id)

        await self._finalize()

    async def _finalize(self) -> None:
        """Poll until the file is written, then persist size/duration/location.

        Egress keeps uploading briefly after it stops, so the first look usually
        shows no file yet. Polling here is safe: the room is already finished.
        """
        for _ in range(_FINALIZE_POLL_ATTEMPTS):
            try:
                state = await asyncio.to_thread(
                    self._egress.get_recording, self._egress_id
                )
            except Exception:
                logger.exception("recording_poll_failed session=%s", self._session_id)
                await self._fail("Recording status could not be confirmed.")
                return

            if state is None:
                await self._fail("LiveKit reported no recording for this interview.")
                return

            status = _map_status(state.status)
            if status in {
                RecordingStatus.COMPLETED,
                RecordingStatus.FAILED,
                RecordingStatus.ABORTED,
            }:
                await self._persist_state(state, default=status)
                logger.info(
                    "recording finalized session=%s status=%s duration=%ss",
                    self._session_id,
                    status.value,
                    state.duration_seconds,
                )
                return

            await asyncio.sleep(_FINALIZE_POLL_INTERVAL_SECONDS)

        # Still not terminal. Leave what we know rather than inventing a state.
        logger.warning(
            "recording did not finalize in time session=%s", self._session_id
        )

    # --- persistence ---------------------------------------------------------

    async def _persist_state(
        self, state: EgressState, *, default: RecordingStatus
    ) -> None:
        status = _map_status(state.status) if state.status else default
        await self._persist(
            InterviewRecordingRecord(
                interview_session_id=self._session_id,
                status=status,
                egress_id=state.egress_id or self._egress_id,
                file_path=recording_filepath(self._session_id),
                file_url=self._public_url(state.location),
                file_size_bytes=state.size_bytes,
                duration_seconds=state.duration_seconds,
                started_at=_utc(state.started_at) or datetime.now(timezone.utc),
                ended_at=_utc(state.ended_at),
                error=state.error,
            )
        )

    def _public_url(self, location: str | None) -> str | None:
        """Playback URL, when a public base is configured.

        The raw egress ``location`` is a storage URI, not necessarily something
        a browser can fetch, so HR playback goes through the configured base URL
        instead of trusting whatever the provider reports.
        """
        base = self._settings.recording_public_base_url
        if not base:
            return None
        return f"{base.rstrip('/')}/{recording_filepath(self._session_id)}"

    async def _fail(self, message: str) -> None:
        await self._persist(
            InterviewRecordingRecord(
                interview_session_id=self._session_id,
                status=RecordingStatus.FAILED,
                egress_id=self._egress_id,
                file_path=recording_filepath(self._session_id),
                ended_at=datetime.now(timezone.utc),
                error=message,
            )
        )
        # Unblock a stop() that is waiting on a start that will never happen.
        self._started.set()

    async def _persist(self, record: InterviewRecordingRecord) -> None:
        try:
            await asyncio.to_thread(self._repository.upsert, record)
        except Exception:
            # Losing the recording's bookkeeping must not end the interview.
            logger.exception("recording_persist_failed session=%s", self._session_id)
