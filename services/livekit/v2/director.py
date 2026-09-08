"""The adapter boundary between the realtime voice core and HR state.

This is the ONLY module the voice core uses to reach HR logic, and every
crossing obeys two rules:

1. **Nothing synchronous runs on the event loop.** ``InterviewAgentService`` is
   synchronous and database-backed; a single ``submit_answer`` can perform
   several round trips and, when the engine decides a follow-up is warranted,
   an LLM call inside the same call. All of it goes through
   ``asyncio.to_thread``. This is what the previous implementation did not do:
   ``CoreInterviewVoiceAgent.on_user_turn_completed`` called
   ``VoiceConversationService.respond()`` inline, so classification, database
   reads and the follow-up LLM all landed between the candidate finishing a
   sentence and the agent making any sound.

2. **The hot path never reads the database.** ``get_current_prompt`` is a
   database call, so it is never used per turn. The current prompt is cached in
   memory and refreshed only when the engine actually moves -- at ``start()``
   and ``advance()``. Per-turn context assembly is a dictionary read.

State transitions remain the engine's alone. This class holds a cursor and a
speech buffer; it never decides what the next question is, never scores, and
never evaluates.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from application.interview_agent_service import InterviewAgentService
from models.agent_persona import VoiceAgentPersona
from models.interview import NextPrompt

logger = logging.getLogger("interview_agent.livekit.v2.director")


@dataclass(frozen=True)
class QuestionView:
    """A read-only snapshot of where the interview is, for prompt assembly."""

    question_id: str
    text: str
    category: str
    index: int  # 1-based, for humans
    total: int
    finished: bool = False

    @property
    def is_last(self) -> bool:
        return self.total > 0 and self.index >= self.total


def _view(prompt: NextPrompt) -> QuestionView:
    return QuestionView(
        question_id=prompt.question_id or "",
        text=prompt.question_text or "",
        category=str(prompt.category or ""),
        index=prompt.question_index + 1,
        total=prompt.total_questions,
        finished=bool(prompt.finished),
    )


class VoiceInterviewDirector:
    """Drives one voice interview. One instance per LiveKit session."""

    def __init__(
        self,
        agent_service: InterviewAgentService,
        *,
        session_id: str,
        persona: VoiceAgentPersona,
        candidate_name: str,
        position_title: str,
        max_follow_ups_per_question: int = 2,
    ) -> None:
        self._service = agent_service
        self._session_id = session_id
        self._persona = persona
        self._candidate_name = candidate_name
        self._position_title = position_title
        self._max_follow_ups = max_follow_ups_per_question

        # Cached cursor. Refreshed only when the engine moves; read freely in
        # the hot path because reading it costs nothing.
        self._current: QuestionView | None = None
        # Verbatim candidate speech for the current question, accumulated
        # across however many conversational turns the answer took. The
        # persisted transcript gets what the candidate actually said, never a
        # model's paraphrase of it.
        self._answer_buffer: list[str] = []
        self._follow_ups_asked = 0
        self._completed = False
        # Set synchronously the instant a candidate-requested termination
        # begins, BEFORE the engine call it triggers is awaited. Everything that
        # asks "is this interview still running?" -- the idle watchdog, the
        # next_question tool, the per-turn path -- reads it through
        # ``is_finished``/``is_terminating`` and therefore sees the interview as
        # over with no scheduling gap in which a nudge or a further question
        # could slip out.
        self._terminating = False

    # --- properties ----------------------------------------------------------

    @property
    def persona(self) -> VoiceAgentPersona:
        return self._persona

    @property
    def candidate_name(self) -> str:
        return self._candidate_name

    @property
    def position_title(self) -> str:
        return self._position_title

    @property
    def is_finished(self) -> bool:
        """True once the interview is over, or the moment it starts ending.

        Deliberately covers the terminating window as well as the completed
        state: the idle watchdog polls this to decide whether to stay quiet, and
        a candidate-requested ending must silence it immediately rather than
        after the engine round trip has come back.
        """
        return self._completed or self._terminating

    @property
    def is_terminating(self) -> bool:
        """True once a candidate-requested termination has begun."""
        return self._terminating

    @property
    def follow_ups_remaining(self) -> int:
        return max(0, self._max_follow_ups - self._follow_ups_asked)

    def current_view(self) -> QuestionView | None:
        """In-memory cursor read. No I/O -- safe in the hot path."""
        return self._current

    # --- hot path (must stay non-blocking) -----------------------------------

    def note_candidate_speech(self, text: str) -> None:
        """Buffer verbatim candidate speech. Append only: no I/O, no LLM."""
        cleaned = (text or "").strip()
        if cleaned:
            self._answer_buffer.append(cleaned)

    def note_follow_up_asked(self) -> None:
        self._follow_ups_asked += 1

    # --- engine transitions (always off the event loop) ----------------------

    async def start(self) -> QuestionView:
        """Move the persisted session to IN_PROGRESS and get question one."""
        prompt = await asyncio.to_thread(self._service.start_interview, self._session_id)
        self._current = _view(prompt)
        self._answer_buffer.clear()
        self._follow_ups_asked = 0
        logger.info(
            "v2 interview started session=%s questions=%d",
            self._session_id,
            self._current.total,
        )
        return self._current

    async def advance(self) -> QuestionView:
        """Submit the buffered answer and move to the next planned question.

        ``submit_answer`` is the expensive one: database writes plus, when the
        engine's own caps allow it, the Interviewer follow-up LLM call. It runs
        in a worker thread, and only at question boundaries -- never on every
        conversational turn.

        ``intent_hint`` is deliberately not passed. The VoiceIntentClassifier is
        excluded from this path, so the engine sees exactly the behaviour a
        text/API caller sees, which is its long-standing default.
        """
        if self._completed or self._terminating:
            return self._finished_view()

        answer = " ".join(self._answer_buffer).strip()
        if not answer:
            # The engine records one turn per submitted answer. An empty string
            # would persist a blank turn and give the evaluator nothing to
            # attribute, so an unanswered question is marked explicitly.
            answer = "[no verbal answer recorded]"

        prompt = await asyncio.to_thread(
            self._service.submit_answer, self._session_id, answer
        )
        self._answer_buffer.clear()
        self._follow_ups_asked = 0

        if prompt.finished:
            self._completed = True
            self._current = self._finished_view()
            logger.info("v2 interview complete session=%s", self._session_id)
            return self._current

        self._current = _view(prompt)
        logger.info(
            "v2 advanced session=%s question=%d/%d",
            self._session_id,
            self._current.index,
            self._current.total,
        )
        return self._current

    async def terminate(self) -> None:
        """End the interview because the candidate asked to stop.

        Uses the SAME terminal transition as every other ending --
        ``InterviewAgentService.end_interview`` -> ``InterviewState.COMPLETED``
        -- which is exactly what the text/V1 path already does for a
        STOP_REQUEST (application/voice_conversation_service.py). No new state
        is invented, and evaluation and report generation stay where they are:
        in the queue worker, after the transport returns.

        The buffered answer for the in-flight question is NOT submitted, again
        matching V1, which discards its pending utterance on a stop request.
        ``submit_answer`` would advance the cursor and can trigger the follow-up
        LLM -- generating a question during the ending is precisely what must
        not happen. The candidate's words are not lost: the speaker-labelled
        voice transcript records them independently of the engine.

        Idempotent, and safe to interleave: ``_terminating`` is set before the
        first await, so a second call returns immediately and the watchdog is
        already silent by the time this yields.
        """
        if self._terminating or self._completed:
            return
        self._terminating = True
        try:
            await asyncio.to_thread(self._service.end_interview, self._session_id)
            self._completed = True
            logger.info("v2 interview terminated by candidate session=%s", self._session_id)
        except Exception:
            # Leave _completed False so teardown's end_if_unfinished() retries
            # the engine transition. _terminating stays True regardless: the
            # conversation is over either way, and nothing may speak again.
            logger.exception(
                "v2 terminate failed to end session=%s", self._session_id
            )

    async def end_if_unfinished(self) -> None:
        """Close out a session the candidate abandoned mid-interview.

        Evaluation and report generation are NOT triggered here. They belong to
        the queue worker, which runs them after the transport returns -- i.e.
        entirely outside spoken-turn latency. This only moves the session out of
        IN_PROGRESS so the worker sees a terminal state.
        """
        if self._completed:
            return
        try:
            await asyncio.to_thread(self._service.end_interview, self._session_id)
            self._completed = True
            logger.info("v2 interview ended early session=%s", self._session_id)
        except Exception:
            # A session that is already COMPLETED (or never started) raises
            # here; that is not an error worth failing a teardown over.
            logger.debug(
                "v2 end_interview skipped session=%s", self._session_id, exc_info=True
            )

    # --- internal ------------------------------------------------------------

    def _finished_view(self) -> QuestionView:
        total = self._current.total if self._current else 0
        return QuestionView(
            question_id="",
            text="",
            category="",
            index=total,
            total=total,
            finished=True,
        )
