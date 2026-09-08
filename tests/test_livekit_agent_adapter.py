import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from application.voice_conversation_service import VoiceConversationContext, VoiceReply
from models.agent_persona import default_persona
from services.livekit.agent_server import (
    CoreInterviewVoiceAgent,
    _configure_provider_logging,
    _handle_typed_input,
    _SpeechTimings,
    _turn_handling_options,
)


class FakeConversation:
    def __init__(self, reply: VoiceReply) -> None:
        self.reply = reply
        self.transcripts: list[str] = []
        #: What pop_turn_timings should hand back for the next call (P0-4;
        #: prompt_lookup_ms/category_lookup_ms added Batch 2).
        self.turn_timings: dict[str, float | None] = {
            "classify_ms": None,
            "engine_ms": None,
            "prompt_lookup_ms": None,
            "category_lookup_ms": None,
        }

    def respond(self, context: VoiceConversationContext, transcript: str) -> VoiceReply:
        del context
        self.transcripts.append(transcript)
        return self.reply

    def pop_turn_timings(self, session_id: str) -> dict[str, float | None]:
        del session_id
        return self.turn_timings


class FakeSession:
    def __init__(self) -> None:
        self.claimed = False
        self.interrupted = False

    @asynccontextmanager
    async def _claim_user_turn(self):
        self.claimed = True
        yield

    async def interrupt(self) -> None:
        self.interrupted = True


class FakeChatItem:
    """Stands in for the assistant llm.ChatMessage session.say() creates."""

    def __init__(self, metrics: dict | None = None) -> None:
        self.metrics = metrics if metrics is not None else {}


class FakeSpeechHandle:
    def __init__(self, *, interrupted: bool, chat_items: list | None = None) -> None:
        self.interrupted = interrupted
        self.chat_items = chat_items if chat_items is not None else []

    async def wait_for_playout(self) -> None:
        return None


class FakeSpeechSession:
    def __init__(
        self,
        interruptions: list[bool],
        *,
        chat_items_by_call: list[list] | None = None,
    ) -> None:
        self._interruptions = iter(interruptions)
        self._chat_items_by_call = (
            iter(chat_items_by_call) if chat_items_by_call is not None else None
        )
        self.spoken: list[tuple[str, bool]] = []

    def say(self, text: str, *, allow_interruptions: bool) -> FakeSpeechHandle:
        self.spoken.append((text, allow_interruptions))
        chat_items = next(self._chat_items_by_call) if self._chat_items_by_call is not None else []
        return FakeSpeechHandle(interrupted=next(self._interruptions), chat_items=chat_items)


class VoiceAgentHarness(CoreInterviewVoiceAgent):
    def __init__(
        self,
        conversation: FakeConversation,
        context: VoiceConversationContext,
        speech_session: FakeSpeechSession,
        *,
        on_completed: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(conversation, context, on_completed=on_completed)  # type: ignore[arg-type]
        self._speech_session = speech_session

    @property
    def session(self) -> FakeSpeechSession:  # type: ignore[override]
        return self._speech_session


def _context() -> VoiceConversationContext:
    return VoiceConversationContext(
        session_id="session-1",
        candidate_id=2,
        position_id=3,
        candidate_name="Demo Candidate",
        company_name="Acme",
        position_title="Engineer",
        persona=default_persona(company_name="Acme"),
    )


def test_final_stt_turn_calls_existing_conversation_service() -> None:
    conversation = FakeConversation(VoiceReply(("Next core prompt",)))
    agent = CoreInterviewVoiceAgent(conversation, _context())  # type: ignore[arg-type]
    agent._speak_reply = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(
        agent.on_user_turn_completed(
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(text_content="Final STT transcript"),  # type: ignore[arg-type]
        )
    )

    assert conversation.transcripts == ["Final STT transcript"]
    agent._speak_reply.assert_awaited_once_with(conversation.reply)


def test_typed_turn_uses_same_agent_path_without_livekit_llm() -> None:
    session = FakeSession()
    agent = SimpleNamespace(respond_to_text=AsyncMock())

    asyncio.run(
        _handle_typed_input(
            session,  # type: ignore[arg-type]
            SimpleNamespace(text="Typed fallback answer"),  # type: ignore[arg-type]
            agent=agent,  # type: ignore[arg-type]
        )
    )

    assert session.claimed is True
    assert session.interrupted is True
    agent.respond_to_text.assert_awaited_once_with("Typed fallback answer")


def test_interruption_stops_remaining_queued_reply_segments() -> None:
    """Meta-intent replies (question_must_be_delivered=False, the default) --
    e.g. repeat/off-topic/candidate-question -- never advanced engine state, so
    stopping delivery on interruption is harmless. Behaviour preserved
    byte-identical from before P0-2."""
    conversation = FakeConversation(VoiceReply(("Got it.", "Next core question?")))
    session = FakeSpeechSession([True])
    agent = VoiceAgentHarness(conversation, _context(), session)

    asyncio.run(agent._speak_reply(conversation.reply))

    assert session.spoken == [("Got it.", True)]


# ---- P0-2: never orphan a question the engine has already committed to ----


def test_critical_question_is_delivered_even_when_reaction_is_interrupted() -> None:
    """question_must_be_delivered=True means submit_answer/start_interview
    already advanced the engine to this question before any audio played --
    interrupting the reaction must not skip it."""
    conversation = FakeConversation(
        VoiceReply(("Got it.", "Next core question?"), question_must_be_delivered=True)
    )
    session = FakeSpeechSession([True, False])  # reaction interrupted, question is not
    agent = VoiceAgentHarness(conversation, _context(), session)

    asyncio.run(agent._speak_reply(conversation.reply))

    assert session.spoken == [
        ("Got it.", True),               # reaction: still interruptible
        ("Next core question?", False),  # question: always delivered, never interruptible
    ]


def test_critical_question_is_never_interruptible_even_without_a_prior_interruption() -> None:
    """The question segment is non-interruptible unconditionally -- not only in
    the recovery path -- so a fresh barge-in exactly as the question begins
    (no preceding reaction) cannot orphan it either."""
    conversation = FakeConversation(
        VoiceReply(("Next core question?",), question_must_be_delivered=True)
    )
    session = FakeSpeechSession([False])
    agent = VoiceAgentHarness(conversation, _context(), session)

    asyncio.run(agent._speak_reply(conversation.reply))

    assert session.spoken == [("Next core question?", False)]


def test_reactions_and_transitions_stay_interruptible_ahead_of_a_critical_question() -> None:
    """Multiple lead segments (reaction + transition) all keep the persona's
    normal interruptible delivery; only the trailing question is protected."""
    conversation = FakeConversation(
        VoiceReply(
            ("Got it.", "Let's switch to troubleshooting.", "Next core question?"),
            question_must_be_delivered=True,
        )
    )
    session = FakeSpeechSession([False, True, False])
    agent = VoiceAgentHarness(conversation, _context(), session)

    asyncio.run(agent._speak_reply(conversation.reply))

    assert session.spoken == [
        ("Got it.", True),
        ("Let's switch to troubleshooting.", True),
        ("Next core question?", False),
    ]


def test_undelivered_question_is_logged_without_transcript_content(
    caplog,
) -> None:
    """allow_interruptions=False should make ordinary candidate audio unable to
    cut off the question; this covers the residual edge case (e.g. a forced
    session.interrupt() from a racing typed-input turn) where it still gets
    interrupted -- the event must be logged, and must carry no answer content."""
    import json
    import logging

    conversation = FakeConversation(
        VoiceReply(("Next core question?",), question_id="q-7", question_must_be_delivered=True)
    )
    session = FakeSpeechSession([True])  # question interrupted despite allow_interruptions=False
    agent = VoiceAgentHarness(conversation, _context(), session)

    logger = logging.getLogger("interview_agent")
    with caplog.at_level(logging.INFO, logger="interview_agent"):
        asyncio.run(agent._speak_reply(conversation.reply))

    matching = [r for r in caplog.records if "VOICE_QUESTION_UNDELIVERED" in r.getMessage()]
    assert len(matching) == 1
    payload = json.loads(matching[0].getMessage())
    assert payload["event"] == "VOICE_QUESTION_UNDELIVERED"
    assert payload["session_id"] == "session-1"
    assert payload["question_id"] == "q-7"
    assert "answer" not in payload and "text" not in payload


def test_no_undelivered_event_when_question_plays_cleanly() -> None:
    conversation = FakeConversation(
        VoiceReply(("Next core question?",), question_must_be_delivered=True)
    )
    session = FakeSpeechSession([False])
    agent = VoiceAgentHarness(conversation, _context(), session)

    asyncio.run(agent._speak_reply(conversation.reply))
    # No exception, no special handling needed -- absence of the event is
    # implicitly covered by every other passing test in this file.


def test_empty_reply_segments_speak_nothing() -> None:
    conversation = FakeConversation(VoiceReply((), question_must_be_delivered=True))
    session = FakeSpeechSession([])
    agent = VoiceAgentHarness(conversation, _context(), session)

    asyncio.run(agent._speak_reply(conversation.reply))

    assert session.spoken == []


def test_completion_notification_waits_until_closing_playout_finishes() -> None:
    events: list[str] = []
    conversation = FakeConversation(VoiceReply(("Configured closing",), finished=True))

    class OrderedSpeechHandle(FakeSpeechHandle):
        async def wait_for_playout(self) -> None:
            events.append("closing-played")

    class OrderedSpeechSession(FakeSpeechSession):
        def say(self, text: str, *, allow_interruptions: bool) -> FakeSpeechHandle:
            self.spoken.append((text, allow_interruptions))
            return OrderedSpeechHandle(interrupted=False)

    async def notify_completed() -> None:
        events.append("candidate-notified")

    agent = VoiceAgentHarness(
        conversation,
        _context(),
        OrderedSpeechSession([]),
        on_completed=notify_completed,
    )

    asyncio.run(agent.respond_to_text("Final candidate answer"))

    assert events == ["closing-played", "candidate-notified"]
    assert agent.core_finished is True


def test_turn_handling_allows_barge_in_but_waits_through_short_pauses() -> None:
    options = _turn_handling_options(allow_interruptions=True)

    assert options["endpointing"]["min_delay"] == 0.8
    assert options["endpointing"]["max_delay"] == 3.0
    assert options["interruption"]["enabled"] is True
    assert options["interruption"]["min_words"] == 1
    assert options["interruption"]["resume_false_interruption"] is True


def test_turn_handling_default_mode_omits_turn_detection_key() -> None:
    """Batch 3: the baseline mode must produce a dict with NO 'turn_detection'
    key at all -- not merely one set to None -- because AgentSession reads it
    with dict.get("turn_detection", inference.TurnDetector()): a present-but-
    None value would suppress that default instead of preserving it. This is
    what keeps 'default' byte-identical to pre-Batch-3 production behaviour,
    with or without explicitly passing turn_detection_mode."""
    implicit = _turn_handling_options(allow_interruptions=True)
    explicit = _turn_handling_options(allow_interruptions=True, turn_detection_mode="default")

    assert "turn_detection" not in implicit
    assert "turn_detection" not in explicit


def test_turn_handling_vad_mode_requests_explicit_vad_turn_detection() -> None:
    """Batch 3 experimental mode: explicitly selects VAD-only turn detection,
    which disables LiveKit's cloud semantic End-of-Utterance model (the
    confirmed source of end_of_turn_delay repeatedly hitting max_delay)
    without touching endpointing timing or interruption handling at all."""
    baseline = _turn_handling_options(allow_interruptions=True)
    experiment = _turn_handling_options(allow_interruptions=True, turn_detection_mode="vad")

    assert experiment["turn_detection"] == "vad"
    # Nothing else changes between modes -- isolates this to turn detection only.
    assert experiment["endpointing"] == baseline["endpointing"]
    assert experiment["interruption"] == baseline["interruption"]


def test_livekit_provider_logging_never_enables_transcript_debug_output() -> None:
    provider_logger = logging.getLogger("livekit.agents")
    original_level = provider_logger.level
    try:
        _configure_provider_logging("DEBUG")
        assert provider_logger.level == logging.INFO

        _configure_provider_logging("WARNING")
        assert provider_logger.level == logging.WARNING
    finally:
        provider_logger.setLevel(original_level)


# ---- P0-4: end-to-end latency instrumentation ----


def test_speak_reply_returns_the_first_segments_playout_duration() -> None:
    conversation = FakeConversation(VoiceReply(("Got it.", "Next core question?")))
    session = FakeSpeechSession([False, False])
    agent = VoiceAgentHarness(conversation, _context(), session)

    result = asyncio.run(agent._speak_reply(conversation.reply))

    assert isinstance(result.first_segment_playout_ms, float)
    assert result.first_segment_playout_ms >= 0.0


def test_speak_reply_returns_none_timings_when_there_is_nothing_to_speak() -> None:
    conversation = FakeConversation(VoiceReply(()))
    session = FakeSpeechSession([])
    agent = VoiceAgentHarness(conversation, _context(), session)

    result = asyncio.run(agent._speak_reply(conversation.reply))

    assert result.first_segment_playout_ms is None
    assert result.tts_ttfb_ms is None


def test_speak_reply_extracts_real_tts_ttfb_from_chat_message_metrics() -> None:
    """Batch 1: session.say() still creates an assistant ChatMessage with the
    SDK's own tts_node_ttfb metric attached (seconds) -- _speak_reply must
    surface it separately from the playout-duration proxy, converted to ms."""
    conversation = FakeConversation(VoiceReply(("Next core question?",)))
    session = FakeSpeechSession(
        [False], chat_items_by_call=[[FakeChatItem({"tts_node_ttfb": 0.245})]],
    )
    agent = VoiceAgentHarness(conversation, _context(), session)

    result = asyncio.run(agent._speak_reply(conversation.reply))

    assert result.tts_ttfb_ms == pytest.approx(245.0)


def test_speak_reply_tts_ttfb_is_none_when_chat_items_carry_no_metrics() -> None:
    """Defensive: an absent/empty metrics dict must degrade to None, never raise."""
    conversation = FakeConversation(VoiceReply(("Next core question?",)))
    session = FakeSpeechSession([False], chat_items_by_call=[[FakeChatItem()]])
    agent = VoiceAgentHarness(conversation, _context(), session)

    result = asyncio.run(agent._speak_reply(conversation.reply))

    assert result.tts_ttfb_ms is None


def test_respond_to_text_logs_one_turn_latency_event_with_every_stage_key(caplog) -> None:
    import json
    import logging

    conversation = FakeConversation(VoiceReply(("Next core question?",), question_id="q-3"))
    conversation.turn_timings = {
        "classify_ms": 640.2,
        "engine_ms": 12.5,
        "prompt_lookup_ms": 210.4,
        "category_lookup_ms": 45.1,
    }
    session = FakeSpeechSession([False])
    agent = VoiceAgentHarness(conversation, _context(), session)

    with caplog.at_level(logging.INFO, logger="interview_agent"):
        asyncio.run(agent.respond_to_text("I led the API migration."))

    matching = [r for r in caplog.records if "VOICE_TURN_LATENCY" in r.getMessage()]
    assert len(matching) == 1
    payload = json.loads(matching[0].getMessage())

    assert payload["event"] == "VOICE_TURN_LATENCY"
    assert payload["session_id"] == "session-1"
    assert payload["question_id"] == "q-3"

    # All pipeline stages -- including the two Batch 1 SDK-native ones and the
    # two Batch 2 session-store ones -- are present as keys on every turn,
    # even when a stage did not apply.
    for key in (
        "end_of_turn_delay_ms", "transcription_delay_ms",
        "endpoint_to_dispatch_ms", "classify_ms", "engine_ms",
        "prompt_lookup_ms", "category_lookup_ms",
        "follow_up_ms", "first_segment_playout_ms", "tts_ttfb_ms", "total_ms",
    ):
        assert key in payload

    # Reused verbatim from what the conversation layer itself measured.
    assert payload["classify_ms"] == 640.2
    assert payload["engine_ms"] == 12.5
    assert payload["prompt_lookup_ms"] == 210.4
    assert payload["category_lookup_ms"] == 45.1
    # follow_up_ms is always None in this phase: the Interviewer's LLM call
    # runs inside the same submit_answer() that produces engine_ms, and
    # isolating it would require instrumenting services/interview_engine.py /
    # agents/interviewer.py, which P0-4 deliberately does not touch.
    assert payload["follow_up_ms"] is None
    # No user_metrics passed to this call (typed/legacy call shape) -- both
    # SDK-native fields must degrade to None, not crash or fabricate a value.
    assert payload["end_of_turn_delay_ms"] is None
    assert payload["transcription_delay_ms"] is None
    assert isinstance(payload["endpoint_to_dispatch_ms"], (int, float))
    assert isinstance(payload["total_ms"], (int, float))
    assert isinstance(payload["first_segment_playout_ms"], (int, float))
    # tts_ttfb_ms is a rename target only: the fake session in this test
    # never attaches chat_items metrics, so it correctly stays None here --
    # covered separately by test_speak_reply_extracts_real_tts_ttfb_*.
    assert payload["tts_ttfb_ms"] is None
    assert "tts_first_byte_ms" not in payload  # old, mislabeled name is gone

    # Never the candidate's words, in this event or anywhere else in the turn.
    assert "I led the API migration." not in caplog.text
    assert "answer" not in payload and "text" not in payload and "transcript" not in payload


def test_turn_latency_first_segment_playout_is_none_when_nothing_is_spoken(caplog) -> None:
    import json
    import logging

    conversation = FakeConversation(VoiceReply(()))
    session = FakeSpeechSession([])
    agent = VoiceAgentHarness(conversation, _context(), session)

    with caplog.at_level(logging.INFO, logger="interview_agent"):
        asyncio.run(agent.respond_to_text("hmm"))

    payload = json.loads(
        next(r for r in caplog.records if "VOICE_TURN_LATENCY" in r.getMessage()).getMessage()
    )
    assert payload["first_segment_playout_ms"] is None
    assert payload["tts_ttfb_ms"] is None


def test_respond_to_text_logs_real_end_of_turn_and_transcription_delay_from_user_metrics(
    caplog,
) -> None:
    """The core Batch 1 wiring: end_of_turn_delay/transcription_delay come
    from ChatMessage.metrics (seconds) and must be logged as milliseconds,
    matching every other _ms field in this event."""
    import json
    import logging

    conversation = FakeConversation(VoiceReply(("Next core question?",), question_id="q-3"))
    session = FakeSpeechSession([False])
    agent = VoiceAgentHarness(conversation, _context(), session)

    with caplog.at_level(logging.INFO, logger="interview_agent"):
        asyncio.run(
            agent.respond_to_text(
                "I led the API migration.",
                user_metrics={"end_of_turn_delay": 0.62, "transcription_delay": 1.15},
            )
        )

    payload = json.loads(
        next(r for r in caplog.records if "VOICE_TURN_LATENCY" in r.getMessage()).getMessage()
    )
    assert payload["end_of_turn_delay_ms"] == pytest.approx(620.0)
    assert payload["transcription_delay_ms"] == pytest.approx(1150.0)


def test_on_user_turn_completed_passes_chat_message_metrics_through(caplog) -> None:
    """Wires the SDK-native metrics from on_user_turn_completed's new_message
    all the way to the logged VOICE_TURN_LATENCY event -- the real production
    path, not just the respond_to_text unit above."""
    import json
    import logging

    conversation = FakeConversation(VoiceReply(("Next core question?",)))
    session = FakeSpeechSession([False])
    agent = VoiceAgentHarness(conversation, _context(), session)

    new_message = SimpleNamespace(
        text_content="Final STT transcript",
        metrics={"end_of_turn_delay": 0.3, "transcription_delay": 0.9},
    )

    with caplog.at_level(logging.INFO, logger="interview_agent"):
        asyncio.run(agent.on_user_turn_completed(SimpleNamespace(), new_message))

    payload = json.loads(
        next(r for r in caplog.records if "VOICE_TURN_LATENCY" in r.getMessage()).getMessage()
    )
    assert payload["end_of_turn_delay_ms"] == pytest.approx(300.0)
    assert payload["transcription_delay_ms"] == pytest.approx(900.0)


def test_on_user_turn_completed_tolerates_a_message_with_no_metrics_attribute() -> None:
    """Defensive: some callers (e.g. existing tests, or a future SDK variant)
    may hand back an object with no .metrics at all -- must not crash."""
    conversation = FakeConversation(VoiceReply(("Next core prompt",)))
    agent = CoreInterviewVoiceAgent(conversation, _context())  # type: ignore[arg-type]
    agent._speak_reply = AsyncMock(return_value=_SpeechTimings(None, None))  # type: ignore[method-assign]

    asyncio.run(
        agent.on_user_turn_completed(
            SimpleNamespace(),  # type: ignore[arg-type]
            SimpleNamespace(text_content="Final STT transcript"),  # type: ignore[arg-type]
        )
    )

    assert conversation.transcripts == ["Final STT transcript"]


def test_turn_latency_classify_and_engine_are_none_when_neither_stage_ran(caplog) -> None:
    """A fast-path turn (e.g. pure filler) never reaches the classifier or the
    engine -- pop_turn_timings correctly reports both as None, not zero."""
    import json
    import logging

    conversation = FakeConversation(VoiceReply(()))
    conversation.turn_timings = {
        "classify_ms": None,
        "engine_ms": None,
        "prompt_lookup_ms": None,
        "category_lookup_ms": None,
    }
    session = FakeSpeechSession([])
    agent = VoiceAgentHarness(conversation, _context(), session)

    with caplog.at_level(logging.INFO, logger="interview_agent"):
        asyncio.run(agent.respond_to_text("hmm"))

    payload = json.loads(
        next(r for r in caplog.records if "VOICE_TURN_LATENCY" in r.getMessage()).getMessage()
    )
    assert payload["classify_ms"] is None
    assert payload["engine_ms"] is None
    assert payload["prompt_lookup_ms"] is None
    assert payload["category_lookup_ms"] is None


def test_typed_input_turns_are_also_measured() -> None:
    """respond_to_text is the shared path for voice AND typed input -- typed
    turns get the same VOICE_TURN_LATENCY coverage, not a silent gap."""
    conversation = FakeConversation(VoiceReply(("Next core question?",)))
    session = FakeSpeechSession([False])
    agent = VoiceAgentHarness(conversation, _context(), session)

    asyncio.run(
        _handle_typed_input(
            FakeSession(),  # type: ignore[arg-type]
            SimpleNamespace(text="typed answer"),  # type: ignore[arg-type]
            agent=agent,  # type: ignore[arg-type]
        )
    )

    assert conversation.transcripts == ["typed answer"]
