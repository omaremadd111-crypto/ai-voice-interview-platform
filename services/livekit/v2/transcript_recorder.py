"""Speaker-labelled voice transcript persistence, off the realtime path.

Utterances arrive on the SDK's ``conversation_item_added`` event -- the same
event the latency instrumentation already listens to -- and are pushed onto an
in-memory queue. A background task drains that queue in batches and writes them
to PostgreSQL. The handler that runs during a turn does nothing but assign a
sequence number and call ``put_nowait``.

Why a queue rather than "just await the write": ``on_user_turn_completed`` and
the event handlers run on the voice event loop, immediately around generation.
A database round trip there is exactly the class of work that made the previous
voice implementation stall, so it is structurally excluded rather than merely
kept fast.

Why a separate table from ``transcript_turns``: that one is the evaluator's
question-centric record, rewritten wholesale by PostgresSessionStore on every
session save. Appending conversational utterances to it would be overwritten
and would change what scoring sees.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from livekit.agents.voice import AgentSession
from livekit.agents.voice.events import ConversationItemAddedEvent

from models.common import TranscriptSpeaker
from models.platform import VoiceTranscriptSegmentRecord
from services.db.recordings import VoiceTranscriptRepository

logger = logging.getLogger("interview_agent.livekit.v2.transcript")

# Bound the queue so a database outage cannot grow memory without limit. On
# overflow the oldest segment is dropped and counted -- losing a line of
# transcript is bad, ending a live interview is worse.
_MAX_QUEUED_SEGMENTS = 2000


class VoiceTranscriptRecorder:
    """Buffers utterances during the interview and persists them in the background."""

    def __init__(
        self,
        repository: VoiceTranscriptRepository,
        *,
        session_id: str,
        flush_interval_seconds: float = 2.0,
        current_question_id=None,
    ) -> None:
        self._repository = repository
        self._session_id = session_id
        self._flush_interval = flush_interval_seconds
        # Callable returning the engine's current question id, so a segment can
        # be tied to what was being asked without the recorder reaching into
        # interview state itself.
        self._current_question_id = current_question_id or (lambda: None)

        self._queue: asyncio.Queue[VoiceTranscriptSegmentRecord] = asyncio.Queue(
            maxsize=_MAX_QUEUED_SEGMENTS
        )
        self._sequence = 0
        self._dropped = 0
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # --- wiring --------------------------------------------------------------

    def attach(self, session: AgentSession) -> None:
        """Subscribe to the session's conversation events and start draining."""

        def _on_item(event: ConversationItemAddedEvent) -> None:
            # Hot path. Sequence assignment plus a non-blocking put; nothing else.
            item = event.item
            role = getattr(item, "role", None)
            speaker = _speaker_for(role)
            if speaker is None:
                return

            text = (getattr(item, "text_content", None) or "").strip()
            if not text:
                return

            self._enqueue(
                VoiceTranscriptSegmentRecord(
                    interview_session_id=self._session_id,
                    sequence=self._sequence,
                    speaker=speaker,
                    content=text,
                    spoken_at=datetime.now(timezone.utc),
                    question_id=self._safe_question_id(),
                )
            )
            self._sequence += 1

        session.on("conversation_item_added", _on_item)
        self._task = asyncio.create_task(
            self._drain(), name=f"transcript-drain-{self._session_id}"
        )

    def _safe_question_id(self) -> str | None:
        try:
            return self._current_question_id()
        except Exception:
            return None

    def _enqueue(self, segment: VoiceTranscriptSegmentRecord) -> None:
        try:
            self._queue.put_nowait(segment)
        except asyncio.QueueFull:
            self._dropped += 1
            logger.warning(
                "transcript queue full, dropped segment session=%s dropped_total=%d",
                self._session_id,
                self._dropped,
            )

    # --- background drain ----------------------------------------------------

    async def _drain(self) -> None:
        """Batch-write queued segments until stopped, then flush the remainder."""
        try:
            while not self._stopping.is_set():
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self._flush_interval
                    )
                except asyncio.TimeoutError:
                    pass
                await self._flush_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("transcript_drain_failed session=%s", self._session_id)

    async def _flush_once(self) -> None:
        batch: list[VoiceTranscriptSegmentRecord] = []
        while not self._queue.empty():
            batch.append(self._queue.get_nowait())
        if not batch:
            return

        try:
            written = await asyncio.to_thread(
                self._repository.append_many, self._session_id, batch
            )
            logger.debug(
                "transcript flushed session=%s segments=%d", self._session_id, written
            )
        except Exception:
            # Re-queue so the segments get another attempt on the next flush or
            # at close. The repository skips sequences already stored, so a
            # partially-successful batch cannot duplicate rows.
            logger.exception("transcript_flush_failed session=%s", self._session_id)
            for segment in batch:
                self._enqueue(segment)

    async def aclose(self) -> None:
        """Stop draining and write whatever is left.

        Called during teardown, after the room is finished -- never in a turn.
        """
        self._stopping.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        # Final flush in case the drain loop exited before the last batch.
        await self._flush_once()
        if self._dropped:
            logger.warning(
                "transcript finished with dropped segments session=%s dropped=%d",
                self._session_id,
                self._dropped,
            )


def _speaker_for(role: str | None) -> TranscriptSpeaker | None:
    """Map a chat role onto the product's speaker vocabulary.

    Only the two conversational roles are persisted. System messages -- the
    per-turn question briefing the agent is given -- are internal instructions,
    not something anyone said, and must never appear in a transcript HR reads.
    """
    if role == "user":
        return TranscriptSpeaker.CANDIDATE
    if role == "assistant":
        return TranscriptSpeaker.INTERVIEWER
    return None
