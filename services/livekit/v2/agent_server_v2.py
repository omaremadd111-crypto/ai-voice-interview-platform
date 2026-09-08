"""V2 realtime voice entrypoint for the full HR product.

Replaces the realtime layer of ``services/livekit/agent_server.py`` while
keeping every contract that the rest of the product depends on:

  * same dispatch: ``agent_name=settings.livekit_agent_name``, explicit
    dispatch, same ``VoiceJobMetadata`` schema from the queue transport;
  * same room and participant identities (``candidate-{candidate_id}``);
  * same completion signal on the ``hr.interview.lifecycle`` data topic, which
    the React candidate page listens for;
  * same persona resolution, including the SPEC 13 AI-disclosure requirement.

What changes is only how a turn is handled. V1 computed the entire reply
synchronously on the event loop and spoke it with ``say()``. V2 puts a
streaming LLM in the session, hands it the engine-owned question as context,
and lets it speak while the engine stays the authority on which question is
current and when the interview ends.

Excluded from this path on purpose: VoiceIntentClassifier, SilenceCoordinator,
the old endpointing configuration, and any synchronous database, scoring,
evaluation or report work.
"""

from __future__ import annotations

import asyncio
import json
import logging

from livekit.agents import (
    Agent,
    AgentServer,
    JobContext,
    RunContext,
    cli,
    function_tool,
)
from livekit.agents.llm import ChatContext, ChatMessage, StopResponse
from livekit.agents.voice import room_io
from pydantic import ValidationError

from application.interview_agent_service import InterviewAgentService
from application.voice_conversation_service import resolve_voice_persona
from config.settings import Settings, load_settings
from models.agent_persona import VoiceAgentPersona
from services.db.agent_configs import SQLAlchemyAgentConfigRepository
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.engine import build_engine, build_session_factory
from services.db.positions import SQLAlchemyPositionRepository
from services.db.postgres_session_store import PostgresSessionStore
from services.db.recordings import (
    SQLAlchemyRecordingRepository,
    SQLAlchemyVoiceTranscriptRepository,
)
from services.livekit.egress_gateway import LiveKitEgressGateway
from services.livekit.agent_server import (
    INTERVIEW_COMPLETED_MESSAGE,
    INTERVIEW_LIFECYCLE_TOPIC,
    VoiceJobMetadata,
    _configure_provider_logging,
)
from services.livekit.v2.director import QuestionView, VoiceInterviewDirector
from services.livekit.v2.idle_watchdog import CandidateIdleWatchdog
from services.livekit.v2.latency import (
    attach_latency_logging,
    attach_turn_state_logging,
)
from services.livekit.v2.recording import InterviewRecorder
from services.livekit.v2.session import build_session
from services.livekit.v2.transcript_recorder import VoiceTranscriptRecorder
from services.logging_service import configure_logging

logger = logging.getLogger("interview_agent.livekit.v2")

#: Returned to the model by whichever tool call closed the interview. The
#: closing has already been spoken verbatim at that point, so the only correct
#: next action is silence.
CLOSING_DELIVERED = (
    "The interview is over and the closing has already been spoken. "
    "Say nothing further."
)


def build_instructions(
    persona: VoiceAgentPersona, candidate_name: str, position_title: str
) -> str:
    """The interviewer's standing brief, derived from the configured persona.

    Persona fields the recruiter can edit (tone, follow-up style, off-topic
    redirection, candidate-question handling) are honoured here; the opening and
    closing scripts are NOT generated -- they are spoken verbatim, see
    ``InterviewVoiceAgentV2.on_enter``.

    The AI-disclosure and prohibited-behaviour constraints are restated to the
    model as hard rules. The persona model already validates disclosure in the
    opening script; this is defence in depth for the rest of the conversation,
    where a generative model could otherwise be talked into claiming to be
    human.
    """
    return f"""You are {persona.agent_name}, {persona.company_name}'s {persona.ai_role_title}, \
conducting a spoken screening interview with {candidate_name} for the \
{position_title} role. Your tone is {persona.tone}.

Non-negotiable rules:
- You are an AI. If asked -- directly or indirectly -- whether you are a human,
  say plainly that you are an AI screening assistant. Never claim or imply
  otherwise, and never adopt a human backstory.
- Never assess or comment on the candidate's emotion, mood, accent, personality
  or any physical or biometric characteristic. You are here to record what they
  say about their experience, nothing else.
- Never give feedback on an answer, never say how they are doing, never hint at
  any outcome or decision. A human recruiter reviews everything afterwards.

How to speak:
- Keep your turns short. One or two sentences, then let them talk.
- Sound like a person talking, not a form being read out. Contractions, plain
  words, natural rhythm.
- Never use markdown, bullet points, numbered lists or emoji. Everything you
  say is spoken aloud.
- Say numbers, dates and abbreviations the way you would say them out loud.

How to listen:
- If they pause mid-thought, that pause is theirs. Do not fill it.
- If they interrupt you, stop and follow where they went.
- If you did not catch something, ask plainly rather than guessing.

How to run the interview:
- You will be told the CURRENT QUESTION before each of your turns. Ask it in
  your own natural words. Do not read it out mechanically and do not announce
  question numbers.
- {persona.follow_up_style}
  You will be told how many follow-ups remain for the current question.
- When the current question has been covered, or you have no follow-ups left,
  call the next_question tool. It gives you the next question to ask.
- If the candidate asks to stop, end, leave, or says they are out of time -- in
  whatever words they use -- call the end_interview tool immediately, before
  saying anything else. Do not compose your own goodbye, do not thank them off
  your own bat, and do not try to talk them into continuing. The closing is
  spoken for you the moment you call it.
- Never invent your own interview questions. The plan is the plan; your
  judgement is about follow-ups and phrasing, not about what gets covered.
- If the candidate asks you something: {persona.candidate_question_handling}
- If the conversation drifts off topic: {persona.off_topic_redirection}

When the tool tells you the interview is complete, stop asking questions and
wait -- the closing is handled for you."""


def question_briefing(view: QuestionView | None, follow_ups_left: int) -> str:
    """Per-turn context injected ahead of the model's reply. Pure string work."""
    if view is None:
        return "INTERVIEW STATUS: not started yet."
    if view.finished:
        return (
            "INTERVIEW STATUS: complete. Every planned question has been covered. "
            "Do not ask anything further."
        )

    lines = [f"CURRENT QUESTION ({view.index} of {view.total}): {view.text}"]
    if view.category:
        lines.append(f"Category: {view.category}")
    if follow_ups_left > 0:
        lines.append(
            f"You may ask up to {follow_ups_left} more follow-up(s) on this question "
            "before calling next_question."
        )
    else:
        lines.append(
            "No follow-ups left on this question. Once they have answered, call "
            "next_question."
        )
    if view.is_last:
        lines.append("This is the final planned question.")
    return "\n".join(lines)


def render_script(
    script: str, *, persona: VoiceAgentPersona, candidate_name: str
) -> str:
    """Fill the persona's script placeholders.

    Mirrors ``VoiceConversationService._render`` exactly, including the
    ``use_candidate_name`` privacy toggle: when a recruiter has turned it off,
    the candidate is addressed as "there" and their name is never spoken. A
    persona configured that way must behave identically on the V2 engine.
    """
    candidate = candidate_name
    if not persona.conversational_style.use_candidate_name:
        candidate = "there"
    first = candidate.split()[0] if candidate.split() else "there"
    replacements = {
        "{candidate_name}": candidate,
        "{candidate_first_name}": first,
        "{agent_name}": persona.agent_name,
        "{company_name}": persona.company_name,
    }
    rendered = script
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


class InterviewVoiceAgentV2(Agent):
    """Conducts the planned interview over the proven V2 pipeline."""

    def __init__(
        self,
        director: VoiceInterviewDirector,
        *,
        on_completed,
        idle_watchdog: CandidateIdleWatchdog | None = None,
    ) -> None:
        super().__init__(
            instructions=build_instructions(
                director.persona, director.candidate_name, director.position_title
            )
        )
        self._director = director
        self._on_completed = on_completed
        # Optional so the agent stays constructible without it (tests, and the
        # VOICE_IDLE_NUDGE_ENABLED=false path).
        self._idle_watchdog = idle_watchdog
        # Claimed exactly once, by whichever path ends the interview first:
        # plan exhaustion via next_question, or a candidate stop request via
        # end_interview. Guarantees one closing and one completion write no
        # matter how many times either tool is called.
        self._closing_claimed = False

    async def on_enter(self) -> None:
        """Speak the persona opening verbatim, then ask question one.

        The opening is delivered with ``say()``, NOT generated. The persona
        model enforces that the opening script discloses the interviewer is an
        AI (SPEC 13); letting a language model paraphrase it would put that
        compliance guarantee at the mercy of a sampler. ``say()`` puts the exact
        approved wording on the wire.

        It is spoken with ``allow_interruptions=False`` so the disclosure cannot
        be barged over before it has been made.
        """
        opening = render_script(
            self._director.persona.opening_script,
            persona=self._director.persona,
            candidate_name=self._director.candidate_name,
        )
        handle = self.session.say(opening, allow_interruptions=False)
        await handle.wait_for_playout()

        view = await self._director.start()
        self.session.generate_reply(
            instructions=(
                "Ask this first interview question in your own natural words. Do not "
                f"re-introduce yourself; you have already greeted them. Question: {view.text}"
            )
        )

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        """Buffer the candidate's words and brief the model for its reply.

        Runs immediately before generation, so it must stay free of I/O. It does
        a list append and a string build -- no database, no LLM, no classifier.
        This is the method whose V1 equivalent blocked the event loop.
        """
        if self._director.is_terminating:
            # The interview is already ending. StopResponse is the SDK's own way
            # to consume a turn without generating a reply, so nothing can be
            # spoken over the closing and no further answer is recorded.
            raise StopResponse()

        spoken = new_message.text_content
        if spoken:
            self._director.note_candidate_speech(spoken)

        turn_ctx.add_message(
            role="system",
            content=question_briefing(
                self._director.current_view(), self._director.follow_ups_remaining
            ),
        )

    def _claim_closing(self) -> bool:
        """Take exclusive ownership of ending the interview. Returns False if
        another path already owns it.

        Runs to completion with no await inside, which is what makes it a real
        latch on a single-threaded event loop: two tool calls cannot both
        observe False, and the watchdog is silenced in the same uninterrupted
        step in which the claim is made. A nudge timer already past its sleep
        cannot slip through either -- close() sets the watchdog's own _closed
        flag, which it re-reads after waking, and there is no await between that
        re-read and its say().
        """
        if self._closing_claimed:
            return False
        self._closing_claimed = True
        # Silence the watchdog before the closing: the interview is over, and
        # the closing's own speaking -> listening transition must not arm a
        # nudge during shutdown.
        if self._idle_watchdog is not None:
            self._idle_watchdog.close()
        return True

    async def _deliver_closing(self) -> None:
        """Speak the recruiter-approved closing once and signal completion.

        Only ever reached through _claim_closing(), so it cannot run twice. The
        closing script is spoken verbatim for the same reason the opening is: it
        is approved wording, and it is what tells the candidate a human will
        review the interview.
        """
        closing = render_script(
            self._director.persona.closing_script,
            persona=self._director.persona,
            candidate_name=self._director.candidate_name,
        )
        handle = self.session.say(closing, allow_interruptions=False)
        await handle.wait_for_playout()
        await self._on_completed()

    @function_tool
    async def next_question(self, context: RunContext) -> str:
        """Record the candidate's answer to the current question and get the next.

        Call this when the current question has been answered well enough, or
        when no follow-ups remain. Do not call it in the middle of an answer.
        """
        # A termination already under way wins: no answer is recorded, the
        # engine cursor does not move, and no question is handed back to be
        # asked over the closing.
        if self._director.is_terminating:
            return CLOSING_DELIVERED

        view = await self._director.advance()

        # A new question gets a fresh nudge budget, and any timer still pending
        # from the previous question is invalidated so it can never speak here.
        if self._idle_watchdog is not None:
            self._idle_watchdog.notify_question_changed()

        if view.finished:
            if not self._claim_closing():
                return CLOSING_DELIVERED
            await self._deliver_closing()
            return CLOSING_DELIVERED

        return (
            f"Next question ({view.index} of {view.total}), ask it in your own natural "
            f"words: {view.text}"
        )

    @function_tool
    async def end_interview(self, context: RunContext) -> str:
        """End the interview now because the candidate asked to stop.

        Call this as soon as the candidate wants to finish early -- they say
        they are out of time, need to leave, want to stop, or do not want to
        continue. Do not say goodbye yourself first; the closing is spoken for
        you. Do not call this when you have simply run out of questions: use
        next_question for that.
        """
        # Both of these are synchronous and happen before the first await, so
        # the watchdog is already silent and a duplicate call already rejected
        # before this coroutine yields control for the engine round trip.
        if not self._claim_closing():
            return CLOSING_DELIVERED

        await self._director.terminate()
        await self._deliver_closing()
        return CLOSING_DELIVERED


#: Settings resolved by the in-process ``build_server()`` call.
#:
#: Present in the parent worker and in a forked child. A spawn/forkserver child
#: re-imports this module rather than re-running ``build_server()``, so it is
#: None there and ``_entrypoint_settings()`` falls back to ``load_settings()``,
#: reading the same environment the parent process was started with.
_configured_settings: Settings | None = None


def _entrypoint_settings() -> Settings:
    """Settings for one job, without capturing anything in a closure.

    The entrypoint must stay a module-level function so the production worker
    can transfer it to a job subprocess by qualified name; a closure over the
    resolved Settings is exactly what made that impossible.
    """
    if _configured_settings is not None:
        return _configured_settings
    return load_settings()


async def voice_interview(ctx: JobContext) -> None:
    """One voice interview job. Module level so it is pickleable by name.

    This function's body is unchanged from when it was nested inside
    ``build_server()``; the only difference is where the Settings come from.
    They were a closure variable, which is precisely what the production worker
    could not transfer to a job subprocess.
    """
    resolved = _entrypoint_settings()
    _configure_provider_logging(resolved.log_level)
    try:
        metadata = VoiceJobMetadata.model_validate(json.loads(ctx.job.metadata or "{}"))
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.error("livekit_invalid_job_metadata error=%s", type(exc).__name__)
        ctx.shutdown("Invalid voice interview metadata")
        return

    engine = build_engine(resolved)
    session_factory = build_session_factory(engine)
    finished = asyncio.Event()

    agent_service = InterviewAgentService(
        settings=resolved,
        session_store=PostgresSessionStore(session_factory),
    )

    # One-time context load, before any audio. Database work here is fine --
    # it is not in a spoken turn. It is still moved off the event loop so
    # the worker stays responsive while it runs.
    def _load_context() -> tuple[VoiceAgentPersona, str, str]:
        candidate = SQLAlchemyCandidateRepository(session_factory).get(
            metadata.candidate_id
        )
        position = SQLAlchemyPositionRepository(session_factory).get(
            metadata.position_id
        )
        if candidate.position_id != metadata.position_id:
            raise ValueError("Candidate and position metadata do not match.")
        persona = resolve_voice_persona(
            SQLAlchemyAgentConfigRepository(session_factory),
            position.owner_id,
            metadata.position_id,
            position.company_name,
        )
        return persona, candidate.full_name, position.title

    try:
        persona, candidate_name, position_title = await asyncio.to_thread(
            _load_context
        )
    except Exception as exc:
        logger.error("v2_context_load_failed error=%s", type(exc).__name__)
        ctx.shutdown("Could not load interview context")
        return

    director = VoiceInterviewDirector(
        agent_service,
        session_id=metadata.session_id,
        persona=persona,
        candidate_name=candidate_name,
        position_title=position_title,
        max_follow_ups_per_question=resolved.max_follow_ups_per_question,
    )

    await ctx.wait_for_participant(identity=f"candidate-{metadata.candidate_id}")

    @ctx.room.on("participant_disconnected")
    def candidate_disconnected(participant: object) -> None:
        if getattr(participant, "identity", None) == f"candidate-{metadata.candidate_id}":
            finished.set()

    async def notify_candidate_completed() -> None:
        """Same lifecycle contract the React candidate page already listens for."""
        await ctx.room.local_participant.publish_data(
            INTERVIEW_COMPLETED_MESSAGE,
            reliable=True,
            destination_identities=[f"candidate-{metadata.candidate_id}"],
            topic=INTERVIEW_LIFECYCLE_TOPIC,
        )
        finished.set()

    session = build_session(resolved, language=persona.language)
    attach_latency_logging(session)
    attach_turn_state_logging(session)

    # --- recording (P0) --------------------------------------------------
    # Started BEFORE session.start() so the persona opening -- which carries
    # the SPEC 13 AI disclosure -- is inside the recording. start() only
    # schedules a background task; nothing about the conversation waits on
    # the egress round trip, and a failure to record never stops the
    # interview.
    recorder: InterviewRecorder | None = None
    if resolved.recording_enabled:
        recorder = InterviewRecorder(
            LiveKitEgressGateway(resolved),
            SQLAlchemyRecordingRepository(session_factory),
            session_id=metadata.session_id,
            settings=resolved,
        )
        recorder.start()

    # --- speaker-labelled transcript (P1) --------------------------------
    # Subscribes to conversation events and drains to PostgreSQL on a
    # background task. The per-utterance handler only assigns a sequence
    # number and enqueues.
    transcript = VoiceTranscriptRecorder(
        SQLAlchemyVoiceTranscriptRepository(session_factory),
        session_id=metadata.session_id,
        flush_interval_seconds=resolved.voice_transcript_flush_interval_seconds,
        current_question_id=lambda: (
            director.current_view().question_id if director.current_view() else None
        ),
    )
    transcript.attach(session)

    # --- candidate idle watchdog -----------------------------------------
    # Speaks fixed nudges when the candidate never starts answering. Reads
    # session state events only; it does not touch turn detection,
    # endpointing, interruption handling or preemptive generation, and it
    # makes no LLM call.
    idle_watchdog: CandidateIdleWatchdog | None = None
    if resolved.voice_idle_nudge_enabled:
        idle_watchdog = CandidateIdleWatchdog(
            session,
            first_nudge_seconds=resolved.voice_idle_first_nudge_seconds,
            second_nudge_seconds=resolved.voice_idle_second_nudge_seconds,
            is_finished=lambda: director.is_finished,
        )
        idle_watchdog.attach()

    try:
        await session.start(
            agent=InterviewVoiceAgentV2(
                director,
                on_completed=notify_candidate_completed,
                idle_watchdog=idle_watchdog,
            ),
            room=ctx.room,
            room_input_options=room_io.RoomInputOptions(pre_connect_audio=True),
        )

        await finished.wait()
    finally:
        # Teardown runs after the room is finished, so these awaits cannot
        # sit inside a spoken turn. Each is independently guarded: a failure
        # finalizing the recording must not prevent the transcript being
        # flushed, or the session being closed out for the queue worker.
        #
        # The watchdog is stopped FIRST and unconditionally: a pending nudge
        # timer must not fire while the rest of teardown is still running.
        if idle_watchdog is not None:
            try:
                await idle_watchdog.aclose()
            except Exception:
                logger.exception(
                    "idle_watchdog_close_failed session=%s", metadata.session_id
                )

        if recorder is not None:
            stop_task = recorder.stop()
            try:
                await asyncio.wait_for(stop_task, timeout=60.0)
            except (asyncio.TimeoutError, Exception):
                logger.warning(
                    "recording did not finalize cleanly session=%s",
                    metadata.session_id,
                )
        try:
            await transcript.aclose()
        except Exception:
            logger.exception(
                "transcript_close_failed session=%s", metadata.session_id
            )

        # If the candidate hung up mid-interview, move the session out of
        # IN_PROGRESS so the queue worker sees a terminal state. Evaluation
        # and report generation stay where they already are -- in the
        # worker, after the transport returns.
        await director.end_if_unfinished()


def build_server(settings: Settings | None = None) -> AgentServer:
    """The V2 worker. Same dispatch contract as V1.

    ``voice_interview`` is registered by reference rather than defined inline.
    It used to be a nested function, which made its qualified name
    ``build_server.<locals>.voice_interview`` -- unresolvable by pickle, so the
    production worker died with "Can't get local object" as soon as it tried to
    hand the entrypoint to a job subprocess. ``rtc_session`` supports direct
    registration, so the wiring is otherwise unchanged.
    """
    global _configured_settings
    resolved = settings or load_settings()
    # Read back by the module-level entrypoint in this process and in a forked
    # child. It is a cache, never the only source: see _entrypoint_settings.
    _configured_settings = resolved
    configure_logging(resolved.log_level)
    _configure_provider_logging(resolved.log_level)
    server = AgentServer()

    server.rtc_session(voice_interview, agent_name=resolved.livekit_agent_name)

    # The registration above is a side effect; the AgentServer itself is what
    # cli.run_app needs. Without this return the function falls off the end,
    # hands run_app None, and startup dies on
    # `NoneType has no attribute 'log_level'` -- run_app reading the server's
    # own log level before it ever looks at the job handler.
    return server


def run_voice_agent_v2() -> None:
    cli.run_app(build_server())
