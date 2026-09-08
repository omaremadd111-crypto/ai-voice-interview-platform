"""Automated LiveKit-layer validation for Phases 2+3: a full SIMULATED voice
session. The real CoreInterviewVoiceAgent adapter drives the real
VoiceConversationService over a real InterviewAgentService (Mock Mode,
in-memory store) from opening line to completed interview -- including
silence-ladder nudges, a no-response advance, a semantic repeat request, and a
don't-know paraphrase -- with no database and no network.

THIS IS NOT PHYSICAL-MICROPHONE VALIDATION. No audio hardware exists in this
environment; real-mic testing remains outstanding for a human (plan section 25).
"""
from types import SimpleNamespace

from agents.voice_intent_classifier import VoiceIntentClassifier
from application.dto import PrepareInterviewRequest
from application.interview_agent_service import InterviewAgentService
from application.voice_conversation_service import VoiceConversationService
from application.voice_silence_coordinator import (
    SilenceCoordinator,
    SilenceLadder,
    SilenceSuggestion,
)
from config.settings import Settings
from models.agent_persona import default_persona
from models.common import ExperienceLevel, InterviewState
from models.platform import AgentConfigRecord, CandidateRecord, Position
from services.llm.mock.mock_service import MockLLMService
from services.livekit.agent_server import CoreInterviewVoiceAgent


class PlayoutSession:
    """Fake AgentSession speech surface: records spoken segments in order."""

    def __init__(self, spoken: list[str]) -> None:
        self.spoken = spoken
        self.handlers: dict[str, object] = {}

    def say(self, text: str, *, allow_interruptions: bool):
        self.spoken.append(text)
        async def wait_for_playout() -> None:
            return None
        return SimpleNamespace(interrupted=False, wait_for_playout=wait_for_playout)

    def on(self, event_name: str, callback) -> None:
        self.handlers[event_name] = callback


class HarnessAgent(CoreInterviewVoiceAgent):
    def __init__(self, conversation, context, spoken: list[str]) -> None:
        super().__init__(conversation, context)  # type: ignore[arg-type]
        self._speech = PlayoutSession(spoken)

    @property
    def session(self):  # type: ignore[override]
        return self._speech


class FakeCandidateRepo:
    def get(self, candidate_id: int) -> CandidateRecord:
        return CandidateRecord(id=4, position_id=3, full_name="Sam Taylor")


class FakePositionRepo:
    def get(self, position_id: int) -> Position:
        return Position(id=3, owner_id=2, company_name="Acme", title="Engineer")


class FakeConfigRepo:
    def __init__(self, persona) -> None:
        self._persona = persona

    def list(self, *, owner_id=None, position_id=None):
        del owner_id, position_id
        return [
            AgentConfigRecord(
                id=1, owner_id=2, position_id=3, name="Aimy",
                config=self._persona.model_dump(mode="json"),
            )
        ]


JD_TEXT = (
    "DEMO DATA - fictional content created for this prototype.\n\n"
    "We are hiring a Junior AI Engineer. Build RAG features over a vector "
    "database, deploy containers, and write tests. Nice to have: cloud."
)
CV_TEXT = (
    "DEMO DATA - fictional content created for this prototype.\n\n"
    "Sam Taylor built python backends and rag pipelines, cut retrieval latency "
    "by 40 percent, and deployed docker containers to cloud."
)


def build_stack_parts():
    """Prepare a real mock-mode session and wire the whole voice stack."""
    settings = Settings()
    agent_service = InterviewAgentService(settings=settings)
    result = agent_service.prepare_interview(
        PrepareInterviewRequest(
            company_name="Acme",
            job_title="Junior AI Engineer",
            experience_level=ExperienceLevel.JUNIOR,
            num_questions=5,
            job_description_text=JD_TEXT,
            candidate_cv_text=CV_TEXT,
            candidate_name="Sam Taylor",
        ),
    )
    session_id = result.session_id
    agent_service.approve_plan(session_id)

    # Record everything the voice layer submits to the engine so transcript
    # fidelity can be asserted end-to-end.
    submitted: list[str] = []
    original_submit = agent_service.submit_answer

    def recording_submit(sid, answer, **kwargs):
        submitted.append(answer)
        return original_submit(sid, answer, **kwargs)

    agent_service.submit_answer = recording_submit  # type: ignore[method-assign]

    classifier = VoiceIntentClassifier(MockLLMService(), timeout_seconds=1.0)
    conversation = VoiceConversationService(
        agent_service,  # type: ignore[arg-type]
        FakeCandidateRepo(),
        FakePositionRepo(),
        FakeConfigRepo(default_persona(company_name="Acme")),
        classifier,
    )
    context = conversation.load_context(
        session_id=session_id, candidate_id=4, position_id=3,
    )
    spoken: list[str] = []
    agent = HarnessAgent(conversation, context, spoken)
    coordinator = SilenceCoordinator(SilenceLadder())

    async def after_reply(reply) -> None:
        if reply.finished:
            coordinator.dispose_session(session_id)
            return
        if reply.question_id is not None:
            coordinator.arm_after_question(session_id, reply.question_id)
        else:
            coordinator.note_conversation_activity(session_id)

    agent._after_reply = after_reply  # type: ignore[attr-defined]
    return agent_service, conversation, context, agent, coordinator, spoken, submitted


async def drive_silence(agent, coordinator, context, spoken, until_seconds=50.0):
    """Tick the watchdog logic exactly as the LiveKit process would."""
    now = 0.0
    fired: list[SilenceSuggestion] = []
    while now < until_seconds:
        now += 0.5
        suggestion = coordinator.check(context.session_id, now)
        if suggestion is None:
            continue
        fired.append(suggestion)
        from services.livekit.agent_server import _act_on_silence_suggestion

        await _act_on_silence_suggestion(
            suggestion,
            coordinator=coordinator,
            session_id=context.session_id,
            conversation=agent._conversation,
            context=context,
            agent=agent,
        )
        if suggestion is SilenceSuggestion.NO_RESPONSE:
            break
    return fired


def test_simulated_full_voice_session_end_to_end() -> None:
    import asyncio

    async def scenario() -> dict:
        (agent_service, conversation, context, agent,
         coordinator, spoken, submitted) = build_stack_parts()

        # Opening + first question play through the real adapter.
        await agent.on_enter()
        assert "Aimy" in spoken[0] or "AI screening" in spoken[0]

        # Turn 1: a substantive answer through the semantic layer.
        await agent.respond_to_text(
            "I led our RAG migration, cut retrieval latency by 40 percent, and "
            "deployed it with docker on our cloud cluster.",
        )

        # Turn 2: a semantic repeat request -- no transcript turn may appear.
        turns_before_repeat = agent_service.get_status(context.session_id).total_turns
        await agent.respond_to_text("Sorry, come again?")
        assert agent_service.get_status(context.session_id).total_turns == (
            turns_before_repeat
        )
        assert "Sorry" not in "".join(submitted)

        # Turn 3: don't-know paraphrase advances without probing.
        turns_before_dont_know = agent_service.get_status(context.session_id).total_turns
        await agent.respond_to_text("I haven't had the chance to use that technology.")
        status = agent_service.get_status(context.session_id)
        assert status.total_turns == turns_before_dont_know + 1

        # Silence ladder: think-pause nudge then no-response advance.
        coordinator.arm_after_question(
            context.session_id,
            agent_service.get_current_prompt(context.session_id).question_id,
        )
        fired = await drive_silence(agent, coordinator, context, spoken)
        assert fired, "silence ladder produced nothing"
        assert SilenceSuggestion.NO_RESPONSE in fired

        # Answer remaining questions literally until completion.
        guard = 0
        while (
            agent_service.get_status(context.session_id).state
            is InterviewState.IN_PROGRESS
        ):
            await agent.respond_to_text(
                f"Closure answer {guard}: I owned the vector database indexes, "
                f"wrote pytest suites, and shipped container images weekly.",
            )
            guard += 1
            assert guard < 20, "interview did not complete"

        status = agent_service.get_status(context.session_id)
        return {"state": status.state, "turns": status.total_turns, "spoken": spoken}

    outcome = asyncio.run(scenario())

    assert outcome["state"] is InterviewState.COMPLETED
    assert outcome["turns"] >= 4
    joined_spoken = " ".join(outcome["spoken"]).lower()
    # No synthetic placeholders anywhere in what Aimy said.
    assert "[no response]" not in joined_spoken
    assert "(silence)" not in joined_spoken
    # Banned vocabulary never spoken.
    for term in ("hire", "reject", "disqualif", "emotion", "accent", "personality"):
        assert term not in joined_spoken
