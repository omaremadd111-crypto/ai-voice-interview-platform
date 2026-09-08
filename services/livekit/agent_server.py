"""LiveKit Agents process adapting STT/TTS to the existing interview core.

This is the only module besides the server gateway that imports the LiveKit SDK.
It supplies no LLM to LiveKit: final STT text is handed to
VoiceConversationService, which calls InterviewAgentService/InterviewEngine,
and the returned core prompt is spoken with TTS.
"""
import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, NamedTuple

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    ChatMessage,
    EndpointingOptions,
    InterruptionOptions,
    JobContext,
    TurnHandlingOptions,
    cli,
    inference,
)
from livekit.agents.llm import MetricsReport
from livekit.agents.voice.room_io import RoomOptions, TextInputEvent, TextInputOptions
from pydantic import BaseModel, ValidationError

from agents.voice_intent_classifier import VoiceIntentClassifier
from application.interview_agent_service import InterviewAgentService
from application.voice_conversation_service import (
    VoiceConversationContext,
    VoiceReply,
    VoiceConversationService,
)
from application.voice_silence_coordinator import (
    SilenceCoordinator,
    SilenceSuggestion,
    build_silence_coordinator,
)
from config.settings import Settings, load_settings
from models.common import InterviewState
from services.db.agent_configs import SQLAlchemyAgentConfigRepository
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.engine import build_engine, build_session_factory
from services.db.positions import SQLAlchemyPositionRepository
from services.db.postgres_session_store import PostgresSessionStore
from services.llm.factory import get_llm_service
from services.logging_service import Event, configure_logging, log_event

_logger = logging.getLogger("interview_agent.livekit")

INTERVIEW_LIFECYCLE_TOPIC = "hr.interview.lifecycle"
INTERVIEW_COMPLETED_MESSAGE = json.dumps(
    {"type": "interview_completed"},
    separators=(",", ":"),
).encode("utf-8")


def _configure_provider_logging(level: str) -> None:
    """Prevent LiveKit SDK diagnostics from emitting candidate transcripts."""
    requested_level = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger("livekit.agents").setLevel(max(logging.INFO, requested_level))


class _SpeechTimings(NamedTuple):
    """P0-4 timing pair returned by ``_speak_reply`` for one delivered reply.

    ``first_segment_playout_ms``: elapsed time from issuing the FIRST
    segment's speech to its playout completing -- includes the full spoken
    duration of that segment, not just synthesis start. This is the original
    P0-2/P0-4 measurement, renamed from the previously misleading
    ``tts_first_byte_ms``: the public SpeechHandle API exposes no lower-level
    "first byte" hook, so this was always a playout-duration proxy, not real
    onset latency -- a longer sentence produces a larger number here even if
    synthesis started instantly.

    ``tts_ttfb_ms``: the SDK's OWN real time-to-first-audio-byte for that same
    first segment, read from the assistant ChatMessage's ``metrics`` (see
    ``_extract_tts_ttfb_ms``) -- genuine synthesis-start latency, independent
    of how long the segment takes to finish speaking.

    Both are None when there were no segments to speak, or when the
    corresponding measurement was unavailable.
    """

    first_segment_playout_ms: float | None
    tts_ttfb_ms: float | None


def _seconds_to_ms(value: float | None) -> float | None:
    return round(value * 1000, 1) if value is not None else None


def _extract_tts_ttfb_ms(handle: Any) -> float | None:
    """Real TTS time-to-first-byte for one spoken segment (Batch 1 addition).

    ``session.say()`` still creates an assistant ChatMessage for the spoken
    segment (add_to_chat_ctx defaults True), and the SDK attaches its own
    ``tts_node_ttfb`` metric to that message once synthesis completes
    (computed via time.perf_counter() internally -- livekit/agents/voice/
    generation.py -- hence the seconds-to-ms conversion here). Unlike
    ``first_segment_playout_ms`` above, this is genuine onset latency.

    Defensive by construction: if the chat item, its metrics, or this
    specific key are ever absent (SDK version drift, a non-message chat
    item), this returns None rather than raising -- a missing value here
    must never break reply delivery.
    """
    for item in getattr(handle, "chat_items", None) or ():
        metrics = getattr(item, "metrics", None)
        if not metrics:
            continue
        ttfb_seconds = metrics.get("tts_node_ttfb")
        if ttfb_seconds is not None:
            return ttfb_seconds * 1000
    return None


class VoiceJobMetadata(BaseModel):
    session_id: str
    queue_item_id: int
    candidate_id: int
    position_id: int


class CoreInterviewVoiceAgent(Agent):
    """LiveKit speech adapter; all prompt decisions come from the core service."""

    def __init__(
        self,
        conversation: VoiceConversationService,
        context: VoiceConversationContext,
        *,
        on_completed: Callable[[], Awaitable[None]] | None = None,
        after_reply: Callable[[VoiceReply], Awaitable[None]] | None = None,
    ) -> None:
        self._conversation = conversation
        self._context = context
        self._on_completed = on_completed
        self._after_reply = after_reply
        self._core_finished = False
        persona = context.persona
        super().__init__(
            instructions=(
                f"You are {persona.agent_name}, {persona.company_name}'s "
                f"{persona.ai_role_title}. Delivery tone: {persona.tone}. "
                "Speak only text returned by the interview application service."
            ),
        )

    async def on_enter(self) -> None:
        reply = self._conversation.begin(self._context)
        await self._speak_reply(reply)
        if self._after_reply is not None:
            # The opening plus the first question just finished playing: the
            # silence ladder for question one starts here.
            await self._after_reply(reply)

    async def on_user_turn_completed(
        self,
        turn_ctx: ChatContext,
        new_message: ChatMessage,
    ) -> None:
        """Route each final STT turn directly to the existing interview core.

        LiveKit calls this hook after endpointing. With no LiveKit-side LLM
        configured, the SDK deliberately stops after this callback, so the only
        generated response is the text returned by InterviewAgentService.
        """
        del turn_ctx
        await self.respond_to_text(
            new_message.text_content,
            user_metrics=getattr(new_message, "metrics", None),
        )

    async def respond_to_text(
        self,
        transcript: str,
        *,
        user_metrics: MetricsReport | None = None,
    ) -> None:
        """Submit one voice or typed turn and synthesize the core-owned reply.

        Also measures and logs this turn's end-to-end latency stages (P0-4):
        timings only, never transcript content. ``endpoint_to_dispatch_ms`` is
        the time from receiving the final transcript (LiveKit already finished
        endpointing before calling this) to having a reply ready to speak --
        i.e. classification + engine processing, whatever combination of them
        this particular turn actually used. classify_ms/engine_ms are that
        same span's own sub-measurements, retrieved from the conversation
        layer where they were actually taken. follow_up_ms is always logged as
        None: the Interviewer's follow-up call runs INSIDE the same
        synchronous submit_answer() that produces engine_ms, and isolating it
        as its own number would require instrumenting
        services/interview_engine.py / agents/interviewer.py, which this
        instrumentation deliberately does not touch -- engine_ms already
        faithfully includes it.
        prompt_lookup_ms/category_lookup_ms (Batch 2) are the remaining
        sub-stages of endpoint_to_dispatch_ms: session-store round trips the
        conversation layer needs to prepare a reply (the current question and
        the upcoming category) that are neither classification nor engine
        work. Together with classify_ms and engine_ms they make
        endpoint_to_dispatch_ms fully attributable -- what is left over after
        subtracting all four is genuine in-process computation (regex
        matching, string building, safety filtering), expected to be small.

        ``user_metrics`` (Batch 1) is the SDK-native ``ChatMessage.metrics``
        for the candidate's turn, only available for real voice turns --
        ``on_user_turn_completed`` passes it through; the typed-input path
        (``_handle_typed_input``) has no LiveKit-side transcription/turn
        detection to measure and leaves it None. It exposes exactly the stage
        this file's own timer cannot see: ``turn_started`` below only starts
        AFTER the SDK has already finished endpointing/transcription and
        handed us final text, so ``end_of_turn_delay`` (time between the
        candidate's actual end of speech and the SDK's decision to end their
        turn) and ``transcription_delay`` (time to obtain the transcript
        after that) were previously invisible to this instrumentation.
        """
        turn_started = time.monotonic()
        reply = self._conversation.respond(self._context, transcript)
        endpoint_to_dispatch_ms = (time.monotonic() - turn_started) * 1000
        timings = self._conversation.pop_turn_timings(self._context.session_id)

        speech_timings = await self.deliver_reply(reply)

        total_ms = (time.monotonic() - turn_started) * 1000
        log_event(
            Event.VOICE_TURN_LATENCY,
            session_id=self._context.session_id,
            question_id=reply.question_id,
            end_of_turn_delay_ms=_seconds_to_ms(
                user_metrics.get("end_of_turn_delay") if user_metrics else None
            ),
            transcription_delay_ms=_seconds_to_ms(
                user_metrics.get("transcription_delay") if user_metrics else None
            ),
            endpoint_to_dispatch_ms=round(endpoint_to_dispatch_ms, 1),
            classify_ms=timings["classify_ms"],
            engine_ms=timings["engine_ms"],
            # Batch 2: sub-stages of endpoint_to_dispatch_ms not already covered
            # by classify_ms/engine_ms -- the session-store round trips
            # (get_current_prompt / get_upcoming_category) the conversation
            # layer needs to prepare a reply. `.get()` because older/fake
            # conversation doubles in tests may not populate these keys.
            prompt_lookup_ms=timings.get("prompt_lookup_ms"),
            category_lookup_ms=timings.get("category_lookup_ms"),
            follow_up_ms=None,
            first_segment_playout_ms=(
                round(speech_timings.first_segment_playout_ms, 1)
                if speech_timings.first_segment_playout_ms is not None
                else None
            ),
            tts_ttfb_ms=(
                round(speech_timings.tts_ttfb_ms, 1)
                if speech_timings.tts_ttfb_ms is not None
                else None
            ),
            total_ms=round(total_ms, 1),
        )

    async def deliver_reply(self, reply: VoiceReply) -> _SpeechTimings:
        """Play a core-owned reply and run the P7/P7.5 completion sequence.

        Single delivery funnel for candidate-triggered replies AND silence-layer
        resolutions, so the lifecycle message always fires only after the
        closing has fully played out (R7) no matter who produced the reply.

        Returns the ``_SpeechTimings`` pair from ``_speak_reply`` (P0-4) for
        callers that want to log it; callers that don't care (the silence
        layer's NO_RESPONSE resolution) simply discard it.
        """
        speech_timings = await self._speak_reply(reply)
        if self._after_reply is not None:
            await self._after_reply(reply)
        if reply.finished:
            # The core has already persisted completion. Notify the browser only
            # after the configured closing has finished playing so it can leave
            # the room without truncating Aimy's final words.
            self._core_finished = True
            if self._on_completed is not None:
                await self._on_completed()
        return speech_timings

    async def speak_only(self, reply: VoiceReply) -> None:
        """Play a silence-layer nudge WITHOUT feeding the activity hooks back.

        A nudge is Aimy talking into continued candidate silence -- it must NOT
        restart the quiet clock, or the ladder could never reach NO_RESPONSE on
        the plan's absolute schedule. Only genuine turns (candidate utterances,
        no-response resolutions) flow through ``deliver_reply``.
        """
        await self._speak_reply(reply)

    async def _speak_reply(self, reply: VoiceReply) -> _SpeechTimings:
        """Speak every segment, protecting an already-committed question (P0-2).

        When ``question_must_be_delivered`` is set, the engine advanced to the
        last segment's question BEFORE any audio played (start_interview or
        submit_answer already ran) -- a barge-in on an earlier reaction/
        transition segment must not skip it, so delivery jumps straight to it
        instead of aborting, and it is spoken with allow_interruptions=False so
        it cannot itself be cut short. Reactions and transitions are unaffected:
        they keep the persona's normal interruptible delivery. Meta-intent
        replies (question_must_be_delivered=False, e.g. repeat/off-topic/
        candidate-question) behave exactly as before P0-2 -- interrupting any
        segment there stops delivery, which is harmless because no engine
        state moved.

        Returns the ``_SpeechTimings`` measured from the FIRST segment only
        (both fields None if there were no segments to speak). Neither
        measurement ever affects any delivery decision; both exist only for
        the caller to log.
        """
        segments = reply.segments
        if not segments:
            return _SpeechTimings(None, None)

        if reply.question_must_be_delivered:
            lead_segments, question_segment = segments[:-1], segments[-1]
        else:
            lead_segments, question_segment = segments, None

        first_segment_ms: float | None = None
        tts_ttfb_ms: float | None = None

        async def _say(segment: str, *, allow_interruptions: bool):
            nonlocal first_segment_ms, tts_ttfb_ms
            started = time.monotonic()
            handle = self.session.say(segment, allow_interruptions=allow_interruptions)
            await handle.wait_for_playout()
            if first_segment_ms is None:
                first_segment_ms = (time.monotonic() - started) * 1000
            if tts_ttfb_ms is None:
                tts_ttfb_ms = _extract_tts_ttfb_ms(handle)
            return handle

        for segment in lead_segments:
            handle = await _say(
                segment,
                allow_interruptions=self._context.persona.conversational_style.allow_interruptions,
            )
            if handle.interrupted:
                break

        if question_segment is None:
            return _SpeechTimings(first_segment_ms, tts_ttfb_ms)

        handle = await _say(question_segment, allow_interruptions=False)
        if handle.interrupted:
            # allow_interruptions=False should make ordinary candidate audio
            # unable to reach this branch; it is only reachable via an explicit
            # forced session.interrupt() elsewhere (e.g. a racing typed-input
            # turn -- see _handle_typed_input). Surface it rather than silently
            # leaving the engine committed to a question the candidate never
            # actually heard.
            log_event(
                Event.VOICE_QUESTION_UNDELIVERED,
                session_id=self._context.session_id,
                question_id=reply.question_id,
            )
        return _SpeechTimings(first_segment_ms, tts_ttfb_ms)

    @property
    def core_finished(self) -> bool:
        return self._core_finished


async def _handle_typed_input(
    session: AgentSession,
    event: TextInputEvent,
    *,
    agent: CoreInterviewVoiceAgent,
) -> None:
    """Make LiveKit chat text follow the same core path as final STT text."""
    # LiveKit's custom text-input contract explicitly uses this context manager
    # to serialize the programmatic user turn and keep barge-in state accurate.
    async with session._claim_user_turn():  # noqa: SLF001
        await session.interrupt()
        await agent.respond_to_text(event.text)


def _language_code(language: str) -> str:
    common = {"english": "en", "arabic": "ar", "french": "fr", "spanish": "es"}
    normalized = language.strip().casefold()
    if normalized in common:
        return common[normalized]
    if len(normalized) in {2, 5}:
        return normalized
    return "en"


def _turn_handling_options(
    *, allow_interruptions: bool, turn_detection_mode: str = "default",
) -> TurnHandlingOptions:
    """Conservative endpointing that tolerates thought pauses without losing barge-in.

    ``turn_detection_mode`` (Batch 3 A/B switch, ``config.settings.Settings
    .voice_turn_detection_mode``):

    - ``"default"`` (baseline, unchanged): the returned dict has no
      ``"turn_detection"`` key at all. LiveKit's own default then applies --
      ``turn_handling.get("turn_detection", inference.TurnDetector())`` --
      which is its cloud-hosted semantic End-of-Utterance model running
      alongside the default Silero VAD. That model can decide an utterance
      is "probably not finished" and force the full ``max_delay`` (3.0s)
      instead of the tuned ``min_delay`` (0.8s); this is the confirmed root
      cause of end_of_turn_delay repeatedly landing at ~3.0s.
    - ``"vad"`` (experimental): explicitly sets ``turn_detection="vad"``,
      which disables that semantic model entirely. Only the (still default,
      unchanged) Silero VAD's silence detection plus this same fixed
      min_delay/max_delay timer decide when a turn ends -- no cloud round
      trip, and no semantic "not finished" prediction can force max_delay.
      Selecting this does not change adaptive interruption: LiveKit only
      disables that for ``turn_detection in ("manual", "realtime_llm")``.
    """
    options: TurnHandlingOptions = TurnHandlingOptions(
        endpointing=EndpointingOptions(
            mode="fixed",
            min_delay=0.8,
            max_delay=3.0,
            alpha=0.9,
        ),
        interruption=InterruptionOptions(
            enabled=allow_interruptions,
            discard_audio_if_uninterruptible=True,
            min_duration=0.5,
            min_words=1,
            resume_false_interruption=True,
            false_interruption_timeout=1.5,
        ),
    )
    if turn_detection_mode == "vad":
        options["turn_detection"] = "vad"
    return options


def _instrument_voice_state_events(session: AgentSession, *, session_id: str) -> None:
    """Phase 0: log LiveKit's own user/agent turn-taking transitions.

    Logs state labels and timestamps only -- UserStateChangedEvent/
    AgentStateChangedEvent never carry transcript text. This is observation
    only: nothing here changes conversation behaviour. It exists so a real
    session run with a physical microphone produces the timing data a later
    silence/presence phase would need, instead of that phase having to guess.
    """

    def _log_transition(event: object) -> None:
        log_event(
            Event.VOICE_STATE_TRANSITION,
            session_id=session_id,
            event_type=getattr(event, "type", type(event).__name__),
            old_state=getattr(event, "old_state", None),
            new_state=getattr(event, "new_state", None),
        )

    session.on("user_state_changed", _log_transition)
    session.on("agent_state_changed", _log_transition)


def build_server(settings: Settings | None = None) -> AgentServer:
    resolved = settings or load_settings()
    configure_logging(resolved.log_level)
    _configure_provider_logging(resolved.log_level)
    server = AgentServer()

    @server.rtc_session(agent_name=resolved.livekit_agent_name)
    async def voice_interview(ctx: JobContext) -> None:
        # The CLI's dev mode may change provider logger levels after the server
        # is built, so re-apply the privacy floor for every assigned room.
        _configure_provider_logging(resolved.log_level)
        try:
            metadata = VoiceJobMetadata.model_validate(json.loads(ctx.job.metadata or "{}"))
        except (json.JSONDecodeError, ValidationError) as exc:
            _logger.error("livekit_invalid_job_metadata error=%s", type(exc).__name__)
            ctx.shutdown("Invalid voice interview metadata")
            return

        engine = build_engine(resolved)
        session_factory = build_session_factory(engine)
        finished = asyncio.Event()
        try:
            agent_service = InterviewAgentService(
                settings=resolved,
                session_store=PostgresSessionStore(session_factory),
            )
            intent_classifier = (
                VoiceIntentClassifier(
                    get_llm_service(resolved),
                    timeout_seconds=resolved.voice_intent_classifier_timeout_seconds,
                )
                if resolved.voice_semantic_layer_enabled
                else None
            )
            conversation = VoiceConversationService(
                agent_service,
                SQLAlchemyCandidateRepository(session_factory),
                SQLAlchemyPositionRepository(session_factory),
                SQLAlchemyAgentConfigRepository(session_factory),
                intent_classifier,
                intent_min_confidence=resolved.voice_intent_min_confidence,
                max_repeats_per_question=resolved.voice_max_repeats_per_question,
            )
            conversation_context = conversation.load_context(
                session_id=metadata.session_id,
                candidate_id=metadata.candidate_id,
                position_id=metadata.position_id,
            )
            await ctx.wait_for_participant(identity=f"candidate-{metadata.candidate_id}")

            # Phase 2 silence / presence layer. Independent kill switch: when
            # VOICE_SILENCE_LAYER_ENABLED=false the coordinator is never built,
            # no timers run, and behaviour is exactly pre-Phase-2.
            coordinator: SilenceCoordinator | None = None
            if resolved.voice_silence_layer_enabled:
                coordinator = build_silence_coordinator(resolved)

            @ctx.room.on("participant_disconnected")
            def candidate_disconnected(participant: object) -> None:
                if getattr(participant, "identity", None) == f"candidate-{metadata.candidate_id}":
                    if coordinator is not None:
                        # R8: a disconnected candidate must never hear a nudge.
                        coordinator.dispose_session(metadata.session_id)
                    finished.set()

            tts_options = build_tts_kwargs(resolved)
            session = AgentSession(
                stt=inference.STT(
                    model=resolved.livekit_stt_model,
                    language=_language_code(conversation_context.persona.language),
                ),
                tts=inference.TTS(**tts_options),
                turn_handling=_turn_handling_options(
                    allow_interruptions=(
                        conversation_context.persona.conversational_style.allow_interruptions
                    ),
                    turn_detection_mode=resolved.voice_turn_detection_mode,
                ),
            )
            # Phase 0 instrumentation only: observes LiveKit's own turn-taking
            # signals (state labels only, never transcript content) so a real
            # session's timing can be measured before any silence/presence
            # behaviour is built on top of these events in a later phase.
            _instrument_voice_state_events(session, session_id=metadata.session_id)
            last_user_state = {"state": "listening"}
            if coordinator is not None:
                _wire_silence_state_events(
                    session,
                    coordinator,
                    session_id=metadata.session_id,
                    last_user_state=last_user_state,
                )

            async def notify_candidate_completed() -> None:
                await ctx.room.local_participant.publish_data(
                    INTERVIEW_COMPLETED_MESSAGE,
                    reliable=True,
                    destination_identities=[f"candidate-{metadata.candidate_id}"],
                    topic=INTERVIEW_LIFECYCLE_TOPIC,
                )
                finished.set()

            async def after_reply_delivered(reply: VoiceReply) -> None:
                """Feed every delivered reply back to the silence layer.

                A new question_id re-arms the ladder with fresh nudge counters;
                delivery-only replies (pause acknowledgements, nudges) simply
                restart the quiet clock; completion disposes the coordinator
                (R8). Metadata only -- nothing here reaches the transcript.
                """
                if coordinator is None or reply.finished:
                    if coordinator is not None:
                        coordinator.dispose_session(metadata.session_id)
                    return
                if reply.question_id is not None:
                    coordinator.arm_after_question(
                        metadata.session_id, reply.question_id, now_seconds=time.monotonic(),
                    )
                else:
                    coordinator.note_conversation_activity(
                        metadata.session_id, now_seconds=time.monotonic(),
                    )

            voice_agent = CoreInterviewVoiceAgent(
                conversation,
                conversation_context,
                on_completed=notify_candidate_completed,
                after_reply=after_reply_delivered,
            )

            await session.start(
                agent=voice_agent,
                room=ctx.room,
                # P6 needs live media transport, not provider-side recording.
                # The persisted application transcript remains authoritative.
                record=False,
                room_options=RoomOptions(
                    text_input=TextInputOptions(
                        text_input_cb=partial(_handle_typed_input, agent=voice_agent),
                    ),
                ),
            )

            watchdog_task: asyncio.Task[None] | None = None
            if coordinator is not None:
                watchdog_task = asyncio.create_task(
                    _silence_watchdog(
                        coordinator,
                        metadata.session_id,
                        conversation=conversation,
                        context=conversation_context,
                        agent=voice_agent,
                        finished=finished,
                        last_user_state=last_user_state,
                        poll_interval_seconds=resolved.voice_silence_poll_interval_seconds,
                    )
                )

            await finished.wait()
            if watchdog_task is not None:
                watchdog_task.cancel()
            if agent_service.get_status(metadata.session_id).state is InterviewState.IN_PROGRESS:
                # A participant that exhausts LiveKit's reconnect window has ended
                # the conversation. Preserve their partial transcript and let the
                # deterministic evaluator represent any missing evidence explicitly.
                agent_service.end_interview(metadata.session_id)
            await session.aclose()
            ctx.shutdown("Voice interview completed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider callback must fail closed
            _logger.error(
                "livekit_voice_job_failed queue_item_id=%s error=%s",
                metadata.queue_item_id,
                type(exc).__name__,
            )
            ctx.shutdown("Voice interview failed")
        finally:
            engine.dispose()

    return server


async def _silence_watchdog(
    coordinator: SilenceCoordinator,
    session_id: str,
    *,
    conversation: VoiceConversationService,
    context: VoiceConversationContext,
    agent: CoreInterviewVoiceAgent,
    finished: asyncio.Event,
    last_user_state: dict[str, str],
    poll_interval_seconds: float,
) -> None:
    """Phase 2 escalation ladder driver (asyncio side of SilenceCoordinator).

    Emits only what the coordinator suggests and always through the same core-
    owned delivery funnel as normal turns: nudges are spoken via
    ``deliver_reply``; NO_RESPONSE goes through VoiceConversationService's
    resolve path, which submits an empty answer (or R5's pending buffer) via
    InterviewEngine and fabricates nothing. The loop exits when the interview
    finishes or the participant disconnects; any internal failure degrades to
    "no more nudges", never to a stuck or looping session (plan section 20).
    """
    try:
        while not finished.is_set():
            await asyncio.sleep(poll_interval_seconds)
            if finished.is_set():
                break
            suggestion = coordinator.check(session_id, time.monotonic())
            if suggestion is None:
                continue
            if last_user_state.get("state") == "speaking":
                # R3: candidate audio started between the check and now -- never
                # talk over an incoming answer. The suggestion is dropped; if
                # the answer does not arrive, later escalations still fire.
                continue
            await _act_on_silence_suggestion(
                suggestion,
                coordinator=coordinator,
                session_id=session_id,
                conversation=conversation,
                context=context,
                agent=agent,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - degrade to pre-Phase-2, never hang
        _logger.error(
            "voice_silence_watchdog_failed session_id=%s error=%s", session_id, type(exc).__name__,
        )


async def _act_on_silence_suggestion(
    suggestion: SilenceSuggestion,
    *,
    coordinator: SilenceCoordinator,
    session_id: str,
    conversation: VoiceConversationService,
    context: VoiceConversationContext,
    agent: CoreInterviewVoiceAgent,
) -> None:
    if suggestion is SilenceSuggestion.THINK_PAUSE:
        await agent.speak_only(VoiceReply(("Take your time.",)))
    elif suggestion is SilenceSuggestion.PRESENCE_CHECK:
        await agent.speak_only(VoiceReply(("Are you still with me?",)))
    elif suggestion is SilenceSuggestion.PRESENCE_RETRY:
        await agent.speak_only(conversation.restate_question(context))
    elif suggestion is SilenceSuggestion.NO_RESPONSE:
        reply = conversation.resolve_no_response(
            context,
            nudge_count=coordinator.nudge_count(session_id),
            elapsed_seconds=coordinator.seconds_since_activity(session_id),
        )
        # The VOICE_NO_RESPONSE event with its timing/nudge fields is logged
        # inside resolve_no_response; this call site delivers through the full
        # funnel so the re-armed ladder / completion sequence run exactly as if
        # the candidate had answered.
        await agent.deliver_reply(reply)


def _wire_silence_state_events(
    session: AgentSession,
    coordinator: SilenceCoordinator,
    *,
    session_id: str,
    last_user_state: dict[str, str],
) -> None:
    """Feed LiveKit user/agent state labels into the silence coordinator.

    R1/R2 live here: user `speaking` and agent `thinking`/`speaking` both pause
    the quiet clock, so neither STT finalisation nor classifier/TTS latency can
    ever be misread as candidate silence. State labels only -- no transcript
    content exists on these events.
    """

    def _on_user_state(event: object) -> None:
        new_state = getattr(event, "new_state", "")
        last_user_state["state"] = new_state
        coordinator.on_user_state_changed(session_id, new_state)

    def _on_agent_state(event: object) -> None:
        coordinator.on_agent_state_changed(session_id, getattr(event, "new_state", ""))

    session.on("user_state_changed", _on_user_state)
    session.on("agent_state_changed", _on_agent_state)


def build_tts_kwargs(settings: Settings) -> dict[str, str]:
    """TTS construction arguments for the configured provider.

    Provider selection is configuration-driven (Settings validated the
    provider/model pair at startup -- see config.settings._resolve_tts_provider);
    this site never hardcodes a provider and never falls back between them.
    Both supported providers (cartesia, inworld) are served by LiveKit
    Inference with the same LiveKit credentials, so the kwargs shape is
    identical regardless of selection.
    """
    tts_kwargs: dict[str, str] = {"model": settings.livekit_tts_model or ""}
    if settings.livekit_tts_voice:
        tts_kwargs["voice"] = settings.livekit_tts_voice
    return tts_kwargs


def run_voice_agent() -> None:
    cli.run_app(build_server())
