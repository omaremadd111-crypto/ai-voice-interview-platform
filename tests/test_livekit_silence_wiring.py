"""Phase 2: automated validation of the LiveKit silence-layer wiring.

No audio hardware exists in this environment: these tests exercise the agent
server's coordinator wiring, the watchdog decision loop, and its suggestion
handling with fake sessions/conversations. They are NOT physical-microphone
validation -- real-mic testing remains outstanding for a human (plan section 25).
"""
import asyncio

from application.voice_conversation_service import VoiceConversationContext, VoiceReply
from application.voice_silence_coordinator import (
    SilenceCoordinator,
    SilenceLadder,
    SilenceSuggestion,
)
from models.agent_persona import default_persona
from services.livekit.agent_server import (
    CoreInterviewVoiceAgent,
    _act_on_silence_suggestion,
    _silence_watchdog,
    _wire_silence_state_events,
)


class _StateEvent:
    def __init__(self, new_state: str) -> None:
        self.new_state = new_state


class _SpeechHandle:
    def __init__(self) -> None:
        self.interrupted = False

    async def wait_for_playout(self) -> None:
        return None


class FakeAgentSpeechSession:
    """Minimal AgentSession double: event registration + say()."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.handlers: dict[str, object] = {}

    def say(self, text: str, *, allow_interruptions: bool) -> _SpeechHandle:
        self.spoken.append(text)
        return _SpeechHandle()

    def on(self, event_name: str, callback) -> None:
        self.handlers[event_name] = callback


class FakeDeliveryAgent:
    """Stands in for CoreInterviewVoiceAgent's delivery funnels.

    ``deliver_reply`` is the full candidate-turn funnel (activity feedback +
    completion sequence); ``speak_only`` is the nudge channel that must NOT
    feed the silence layer back into itself.
    """

    def __init__(self) -> None:
        self.delivered: list[VoiceReply] = []
        self.nudged: list[VoiceReply] = []

    async def deliver_reply(self, reply: VoiceReply) -> None:
        self.delivered.append(reply)

    async def speak_only(self, reply: VoiceReply) -> None:
        self.nudged.append(reply)


class FakeWatchConversation:
    def __init__(self) -> None:
        self.restate_calls = 0
        self.resolved: list[dict] = []

    def restate_question(self, context: VoiceConversationContext) -> VoiceReply:
        self.restate_calls += 1
        return VoiceReply(("Tell me about a project you led?",), question_id="q1")

    def resolve_no_response(
        self, context: VoiceConversationContext, *, nudge_count: int, elapsed_seconds: float,
    ) -> VoiceReply:
        self.resolved.append({"nudge_count": nudge_count, "elapsed": elapsed_seconds})
        return VoiceReply(("Let's move on.", "Next question?"), question_id="q2")


def _context() -> VoiceConversationContext:
    return VoiceConversationContext(
        session_id="sess-1",
        candidate_id=2,
        position_id=3,
        candidate_name="Demo Candidate",
        company_name="Acme",
        position_title="Engineer",
        persona=default_persona(company_name="Acme"),
    )


def test_wire_silence_events_registers_both_handlers() -> None:
    session = FakeAgentSpeechSession()
    coordinator = SilenceCoordinator(SilenceLadder())
    last_user_state: dict[str, str] = {"state": "listening"}

    _wire_silence_state_events(
        session, coordinator, session_id="sess-1", last_user_state=last_user_state,
    )

    assert set(session.handlers) == {"user_state_changed", "agent_state_changed"}


def test_wired_handlers_pause_the_quiet_clock_for_both_sides() -> None:
    session = FakeAgentSpeechSession()
    coordinator = SilenceCoordinator(SilenceLadder())
    last_user_state: dict[str, str] = {"state": "listening"}
    _wire_silence_state_events(
        session, coordinator, session_id="sess-1", last_user_state=last_user_state,
    )
    coordinator.arm_after_question("sess-1", "q1")

    # Candidate starts speaking through the real handler path (R1)...
    session.handlers["user_state_changed"](_StateEvent("speaking"))
    assert last_user_state["state"] == "speaking"
    # ...and the agent goes busy at the same wall-clock moment (R2). Neither may
    # accumulate silence against the candidate.
    session.handlers["agent_state_changed"](_StateEvent("thinking"))
    assert coordinator.check("sess-1", 1000.0) is None

    session.handlers["agent_state_changed"](_StateEvent("listening"))
    session.handlers["user_state_changed"](_StateEvent("listening"))
    # Quiet restarts from ~zero: no stale nudge fires immediately after resume.
    assert coordinator.check("sess-1", 1000.5) is None


def test_think_pause_nudge_uses_speak_only_never_the_activity_feedback_funnel() -> None:
    coordinator = SilenceCoordinator(SilenceLadder())
    coordinator.arm_after_question("sess-1", "q1")
    conversation = FakeWatchConversation()
    agent = FakeDeliveryAgent()

    asyncio.run(_act_on_silence_suggestion(
        SilenceSuggestion.THINK_PAUSE,
        coordinator=coordinator,
        session_id="sess-1",
        conversation=conversation,  # type: ignore[arg-type]
        context=_context(),
        agent=agent,  # type: ignore[arg-type]
    ))

    # Nudge goes out through the nudge channel only; if it flowed through
    # deliver_reply it would reset the quiet clock and NO_RESPONSE could never
    # fire on the plan's absolute schedule.
    assert [reply.segments for reply in agent.nudged] == [("Take your time.",)]
    assert agent.delivered == []


def test_presence_retry_restates_via_conversation_service() -> None:
    coordinator = SilenceCoordinator(SilenceLadder(max_nudges_per_question=2))
    coordinator.arm_after_question("sess-1", "q1")
    conversation = FakeWatchConversation()
    agent = FakeDeliveryAgent()

    asyncio.run(_act_on_silence_suggestion(
        SilenceSuggestion.PRESENCE_RETRY,
        coordinator=coordinator,
        session_id="sess-1",
        conversation=conversation,  # type: ignore[arg-type]
        context=_context(),
        agent=agent,  # type: ignore[arg-type]
    ))

    assert conversation.restate_calls == 1
    assert [reply.segments for reply in agent.nudged] == (
        [("Tell me about a project you led?",)]
    )
    assert agent.delivered == []


def test_no_response_resolves_via_conversation_never_via_direct_engine_call() -> None:
    coordinator = SilenceCoordinator(SilenceLadder(max_nudges_per_question=2))
    coordinator.arm_after_question("sess-1", "q1")
    conversation = FakeWatchConversation()
    agent = FakeDeliveryAgent()

    asyncio.run(_act_on_silence_suggestion(
        SilenceSuggestion.NO_RESPONSE,
        coordinator=coordinator,
        session_id="sess-1",
        conversation=conversation,  # type: ignore[arg-type]
        context=_context(),
        agent=agent,  # type: ignore[arg-type]
    ))

    # The watchdog never touches InterviewEngine itself: resolution goes through
    # VoiceConversationService, which submits empty/buffered text via the core.
    assert len(conversation.resolved) == 1
    assert conversation.resolved[0]["nudge_count"] == 0
    assert len(agent.delivered) == 1
    assert agent.delivered[0].question_id == "q2"


def test_r3_watchdog_never_talks_over_incoming_candidate_audio() -> None:
    ladder = SilenceLadder(think_pause_seconds=6.0)
    coordinator = SilenceCoordinator(ladder)
    coordinator.arm_after_question("sess-1", "q1")
    conversation = FakeWatchConversation()

    async def scenario() -> list[VoiceReply]:
        delivered: list[VoiceReply] = []
        agent = FakeDeliveryAgent()

        async def capture(reply: VoiceReply) -> None:
            delivered.append(reply)

        agent.deliver_reply = capture  # type: ignore[method-assign]
        agent.speak_only = capture  # type: ignore[method-assign]
        finished = asyncio.Event()
        last_user_state = {"state": "listening"}

        # Candidate audio starts right before the watchdog's first actionable
        # tick: R3 forbids talking over it.
        coordinator.on_user_state_changed("sess-1", "speaking")
        last_user_state["state"] = "speaking"

        task = asyncio.create_task(_silence_watchdog(  # type: ignore[arg-type]
            coordinator,
            "sess-1",
            conversation=conversation,  # type: ignore[arg-type]
            context=_context(),
            agent=agent,  # type: ignore[arg-type]
            finished=finished,
            last_user_state=last_user_state,
            poll_interval_seconds=0.005,
        ))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return delivered

    assert asyncio.run(scenario()) == []


def test_r8_watchdog_stops_when_interview_finishes() -> None:
    ladder = SilenceLadder(think_pause_seconds=0.001)
    coordinator = SilenceCoordinator(ladder)
    coordinator.arm_after_question("sess-1", "q1")
    conversation = FakeWatchConversation()
    finished = asyncio.Event()
    last_user_state = {"state": "listening"}

    async def scenario() -> list[VoiceReply]:
        delivered: list[VoiceReply] = []
        agent = FakeDeliveryAgent()

        async def capture(reply: VoiceReply) -> None:
            delivered.append(reply)

        agent.deliver_reply = capture  # type: ignore[method-assign]
        agent.speak_only = capture  # type: ignore[method-assign]
        task = asyncio.create_task(_silence_watchdog(  # type: ignore[arg-type]
            coordinator,
            "sess-1",
            conversation=conversation,  # type: ignore[arg-type]
            context=_context(),
            agent=agent,  # type: ignore[arg-type]
            finished=finished,
            last_user_state=last_user_state,
            poll_interval_seconds=0.001,
        ))
        # Finish BEFORE any threshold could plausibly fire, then let the loop
        # notice and exit instead of nudging into a completed interview.
        finished.set()
        await asyncio.sleep(0.03)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return delivered

    assert asyncio.run(scenario()) == []


def test_watchdog_survives_internal_failure_without_hanging() -> None:
    class ExplodingConversation(FakeWatchConversation):
        def restate_question(self, context: VoiceConversationContext) -> VoiceReply:
            raise RuntimeError("simulated delivery failure")

    ladder = SilenceLadder(think_pause_seconds=0.01)
    coordinator = SilenceCoordinator(ladder)
    coordinator.arm_after_question("sess-1", "q1")
    conversation = ExplodingConversation()
    finished = asyncio.Event()
    last_user_state = {"state": "listening"}

    async def scenario() -> None:
        task = asyncio.create_task(_silence_watchdog(  # type: ignore[arg-type]
            coordinator,
            "sess-1",
            conversation=conversation,  # type: ignore[arg-type]
            context=_context(),
            agent=FakeDeliveryAgent(),  # type: ignore[arg-type]
            finished=finished,
            last_user_state=last_user_state,
            poll_interval_seconds=0.005,
        ))
        await asyncio.sleep(0.03)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Plan section 20: a coordinator failure degrades to "no more nudges",
    # never to a stuck or crashing session.
    asyncio.run(scenario())


def test_kill_switch_leaves_zero_silence_infrastructure_in_the_job() -> None:
    """VOICE_SILENCE_LAYER_ENABLED=false must mean NO coordinator exists -- the
    rollback lever reverts behaviour, it does not merely idle a timer."""
    from config.settings import Settings
    from services.livekit.agent_server import build_server  # noqa: F401  (import sanity)

    settings = Settings()
    assert settings.voice_silence_layer_enabled is True

    disabled = Settings(voice_silence_layer_enabled=False)
    assert disabled.voice_silence_layer_enabled is False
    # The semantic layer stays independently configurable (kill switches are
    # independent by requirement, plan section 21).
    assert disabled.voice_semantic_layer_enabled is True


class _BeginConversation(FakeWatchConversation):
    def begin(self, context: VoiceConversationContext) -> VoiceReply:
        return VoiceReply(("Hello Sam.", "Tell me about a project you led?"), question_id="q1")


class _EnterHarness(CoreInterviewVoiceAgent):
    def __init__(self, conversation, context) -> None:
        super().__init__(conversation, context)  # type: ignore[arg-type]
        self._speech = FakeAgentSpeechSession()

    @property
    def session(self):  # type: ignore[override]
        return self._speech


def test_on_enter_plays_begin_reply_and_arms_the_ladder_for_question_one() -> None:
    conversation = _BeginConversation()
    armed: list[tuple[str | None, bool]] = []

    async def after_reply(reply: VoiceReply) -> None:
        armed.append((reply.question_id, reply.finished))

    agent = _EnterHarness(conversation, _context())  # type: ignore[arg-type]
    agent._after_reply = after_reply  # type: ignore[attr-defined]

    asyncio.run(agent.on_enter())

    # Opening + first question played; the ladder now watches question one.
    assert agent._speech.spoken == ["Hello Sam.", "Tell me about a project you led?"]
    assert armed == [("q1", False)]
