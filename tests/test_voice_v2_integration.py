"""Regression tests for the V2 realtime voice core in the full HR product.

Four groups, ordered by what would hurt most if it broke:

1. Realtime behaviour stays aligned with the validated baseline.
2. Nothing that would reintroduce realtime latency is in the hot path.
3. HR state transitions stay deterministic and engine-owned.
4. The contracts the rest of the product depends on are unchanged, and V1
   remains available for rollback.
"""

from __future__ import annotations

import asyncio
import inspect
import threading

import pytest

from config.settings import Settings
from models.agent_persona import default_persona
from models.common import QuestionCategory
from models.interview import NextPrompt
from services.livekit.v2 import agent_server_v2 as v2
from services.livekit.v2.director import QuestionView, VoiceInterviewDirector
from services.livekit.v2.turn_tuning import build_turn_handling

# The exact configuration validated through real voice testing.
# Pinned as literals so drift is caught here rather than by ear in a real call.
V2_ENDPOINTING = {
    "mode": "dynamic",
    "min_delay": 0.4,
    "max_delay": 2.0,
    "alpha": 0.9,
}
V2_INTERRUPTION = {
    "enabled": True,
    "mode": "adaptive",
    "discard_audio_if_uninterruptible": True,
    "min_duration": 0.4,
    "min_words": 1,
    "resume_false_interruption": True,
    "false_interruption_timeout": 2.0,
    "backchannel_boundary": (1.0, 1.0),
}
V2_PREEMPTIVE = {
    "enabled": True,
    "preemptive_tts": True,
    "max_speech_duration": 10.0,
    "max_retries": 3,
}


@pytest.fixture(autouse=True)
def _isolate_env(isolated_environ) -> None:
    """Every test in this module runs against a private copy of os.environ.

    Several tests here call ``build_server()`` with no arguments, which is the
    real startup path and therefore calls ``load_settings()`` ->
    ``load_dotenv(override=False)``. That permanently copies previously-absent
    keys from the project's .env into os.environ for the rest of the session,
    which silently changes what env-driven tests elsewhere (notably the TTS
    provider suite) observe. conftest's ``isolated_environ`` swaps in a private
    copy so the leak dies with the test.
    """


class FakeAgentService:
    """Stands in for InterviewAgentService.

    Records the thread each call ran on, so the tests can prove the synchronous
    HR layer never executes on the event loop.
    """

    def __init__(self, total: int = 3) -> None:
        self.total = total
        self.index = 0
        self.answers: list[str] = []
        self.threads: dict[str, int] = {}
        self.ended = False

    def _prompt(self, finished: bool = False) -> NextPrompt:
        return NextPrompt(
            state="IN_PROGRESS" if not finished else "COMPLETED",
            question_id=f"q{self.index + 1}",
            question_text=f"Question {self.index + 1} text",
            category=QuestionCategory.TECHNICAL,
            question_index=self.index,
            total_questions=self.total,
            progress_pct=0.0,
            finished=finished,
        )

    def start_interview(self, session_id: str) -> NextPrompt:
        self.threads["start_interview"] = threading.get_ident()
        self.index = 0
        return self._prompt()

    def submit_answer(self, session_id: str, answer: str, **kwargs) -> NextPrompt:
        self.threads["submit_answer"] = threading.get_ident()
        self.answers.append(answer)
        self.index += 1
        return self._prompt(finished=self.index >= self.total)

    def get_current_prompt(self, session_id: str) -> NextPrompt:
        self.threads["get_current_prompt"] = threading.get_ident()
        return self._prompt()

    def end_interview(self, session_id: str):
        self.threads["end_interview"] = threading.get_ident()
        self.ended = True
        return None


def make_director(service: FakeAgentService | None = None) -> VoiceInterviewDirector:
    return VoiceInterviewDirector(
        service or FakeAgentService(),
        session_id="s-1",
        persona=default_persona("Aimy", "FlairsTech"),
        candidate_name="Omar Sayed",
        position_title="Junior AI Engineer",
        max_follow_ups_per_question=2,
    )


# ===========================================================================
# 1. Realtime behaviour equals V2 tag hr-voice-baseline-v1
# ===========================================================================


def test_endpointing_matches_the_v2_tag() -> None:
    assert build_turn_handling(Settings())["endpointing"] == V2_ENDPOINTING


def test_max_endpointing_delay_is_two_seconds() -> None:
    """Called out explicitly in the integration requirements."""
    assert Settings().voice_max_endpointing_delay == 2.0
    assert build_turn_handling(Settings())["endpointing"]["max_delay"] == 2.0


def test_interruption_matches_the_v2_tag() -> None:
    assert build_turn_handling(Settings())["interruption"] == V2_INTERRUPTION


def test_adaptive_interruption_and_min_words_are_preserved() -> None:
    interruption = build_turn_handling(Settings())["interruption"]
    assert interruption["mode"] == "adaptive"
    assert interruption["min_words"] == 1


def test_false_interruption_resume_is_preserved() -> None:
    interruption = build_turn_handling(Settings())["interruption"]
    assert interruption["resume_false_interruption"] is True
    assert interruption["false_interruption_timeout"] == 2.0


def test_preemptive_generation_is_preserved() -> None:
    assert build_turn_handling(Settings())["preemptive_generation"] == V2_PREEMPTIVE


def test_semantic_dynamic_turn_detection_is_used() -> None:
    """A semantic detector, not the old fixed/VAD-only endpointing."""
    turn_detection = build_turn_handling(Settings())["turn_detection"]
    assert type(turn_detection).__name__ == "TurnDetector"
    assert build_turn_handling(Settings())["endpointing"]["mode"] == "dynamic"


def test_old_fixed_endpointing_is_not_used_by_v2() -> None:
    """V1 used mode="fixed" with 0.8/3.0. That must not reach the V2 path."""
    from services.livekit.agent_server import _turn_handling_options

    v1 = _turn_handling_options(allow_interruptions=True)["endpointing"]
    v2_endpointing = build_turn_handling(Settings())["endpointing"]

    assert v1["mode"] == "fixed"
    assert v2_endpointing["mode"] == "dynamic"
    assert v2_endpointing != v1


def test_session_has_a_streaming_llm_and_vad(monkeypatch) -> None:
    """The structural difference from V1, which had neither.

    Without an LLM in the session there is nothing to stream and nothing for
    preemptive generation to run early, so this is what makes the tuned
    behaviour reachable at all.
    """
    from services.livekit.v2.session import build_session

    # LiveKit Inference reads credentials from the environment, not from the
    # Settings object, so they must be present for construction to succeed.
    monkeypatch.setenv("LIVEKIT_URL", "ws://localhost:7880")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "devsecret" * 4)
    settings = Settings()

    async def _build():
        return build_session(settings)

    session = asyncio.run(_build())

    assert session.llm is not None
    assert session.vad is not None
    assert session.stt is not None
    assert session.tts is not None
    assert session.options.endpointing == V2_ENDPOINTING
    assert session.options.interruption["min_words"] == 1


# ===========================================================================
# 2. Nothing reintroduces realtime latency
# ===========================================================================


def test_engine_calls_run_off_the_event_loop() -> None:
    """The core fix. V1 ran all of this inline and stalled audio."""
    service = FakeAgentService()
    director = make_director(service)

    async def drive() -> int:
        loop_thread = threading.get_ident()
        await director.start()
        director.note_candidate_speech("An answer.")
        await director.advance()
        return loop_thread

    loop_thread = asyncio.run(drive())

    assert service.threads["start_interview"] != loop_thread
    assert service.threads["submit_answer"] != loop_thread


def test_hot_path_never_reads_the_database() -> None:
    """on_user_turn_completed must not call get_current_prompt.

    That is a database round trip; the cursor is cached in memory and refreshed
    only when the engine actually moves.
    """
    service = FakeAgentService()
    director = make_director(service)

    asyncio.run(director.start())
    service.threads.pop("get_current_prompt", None)

    for _ in range(5):
        director.note_candidate_speech("More answer.")
        view = director.current_view()
        v2.question_briefing(view, director.follow_ups_remaining)

    assert "get_current_prompt" not in service.threads


def test_hot_path_helpers_do_no_io() -> None:
    """note_candidate_speech and question_briefing are pure."""
    source = inspect.getsource(VoiceInterviewDirector.note_candidate_speech)
    assert "to_thread" not in source
    assert "self._service" not in source

    briefing = inspect.getsource(v2.question_briefing)
    assert "to_thread" not in briefing
    assert "_service" not in briefing


def _imported_names(module_name: str) -> set[str]:
    """Every module and symbol a module actually imports.

    Parsed from the AST rather than grepped from the source: the V2 modules
    *document* which components they exclude, so a text search for the name
    matches the prose that explains the exclusion. Only a real import counts.
    """
    import ast

    module = __import__(module_name, fromlist=["x"])
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            for alias in node.names:
                names.add(alias.name)
    return names


V2_MODULES = (
    "services.livekit.v2.agent_server_v2",
    "services.livekit.v2.director",
    "services.livekit.v2.session",
    "services.livekit.v2.turn_tuning",
    "services.livekit.v2.latency",
)


def test_intent_classifier_is_not_imported_by_the_v2_path() -> None:
    """VoiceIntentClassifier is excluded from the realtime path."""
    for module_name in V2_MODULES:
        imported = _imported_names(module_name)
        assert "VoiceIntentClassifier" not in imported, module_name
        assert "agents.voice_intent_classifier" not in imported, module_name


def test_silence_coordinator_is_not_imported_by_the_v2_path() -> None:
    for module_name in V2_MODULES:
        imported = _imported_names(module_name)
        assert "SilenceCoordinator" not in imported, module_name
        assert "build_silence_coordinator" not in imported, module_name
        assert "application.voice_silence_coordinator" not in imported, module_name


def test_v2_does_not_import_the_old_conversation_orchestrator_hot_path() -> None:
    """resolve_voice_persona is a one-time helper and is the ONLY thing taken
    from the old conversation service. VoiceConversationService itself -- the
    synchronous per-turn orchestrator -- must not be imported."""
    for module_name in V2_MODULES:
        imported = _imported_names(module_name)
        assert "VoiceConversationService" not in imported, module_name


def test_no_scoring_evaluation_or_report_in_the_voice_path() -> None:
    """Expensive work must stay in the queue worker, after the call ends."""
    for module_name in (
        "services.livekit.v2.agent_server_v2",
        "services.livekit.v2.director",
    ):
        source = inspect.getsource(__import__(module_name, fromlist=["x"]))
        assert "evaluate_interview" not in source
        assert "generate_report" not in source


def test_evaluation_and_report_still_run_in_the_queue_worker() -> None:
    """The other half of the same guarantee: they did not get dropped."""
    from application import queue_worker

    source = inspect.getsource(queue_worker)
    assert "evaluate_interview" in source
    assert "generate_report" in source


# ===========================================================================
# 3. HR state transitions stay deterministic and engine-owned
# ===========================================================================


def test_interview_walks_the_plan_one_question_at_a_time() -> None:
    service = FakeAgentService(total=3)
    director = make_director(service)

    async def drive() -> list[str]:
        seen = [(await director.start()).question_id]
        for _ in range(3):
            director.note_candidate_speech("Answer.")
            view = await director.advance()
            if view.finished:
                break
            seen.append(view.question_id)
        return seen

    seen = asyncio.run(drive())

    assert seen == ["q1", "q2", "q3"]
    assert director.is_finished


def test_verbatim_speech_is_buffered_across_turns() -> None:
    """The persisted answer is what the candidate said, not a paraphrase."""
    service = FakeAgentService()
    director = make_director(service)

    async def drive() -> None:
        await director.start()
        director.note_candidate_speech("I built a search system.")
        director.note_candidate_speech("It used embeddings.")
        await director.advance()

    asyncio.run(drive())

    assert len(service.answers) == 1
    assert "search system" in service.answers[0]
    assert "embeddings" in service.answers[0]


def test_buffer_resets_between_questions() -> None:
    service = FakeAgentService()
    director = make_director(service)

    async def drive() -> None:
        await director.start()
        director.note_candidate_speech("First.")
        await director.advance()
        director.note_candidate_speech("Second.")
        await director.advance()

    asyncio.run(drive())

    assert "First." in service.answers[0]
    assert "First." not in service.answers[1]


def test_unanswered_question_is_recorded_explicitly() -> None:
    service = FakeAgentService()
    director = make_director(service)

    async def drive() -> None:
        await director.start()
        await director.advance()

    asyncio.run(drive())

    assert service.answers[0] == "[no verbal answer recorded]"


def test_advancing_past_the_end_is_safe_and_records_nothing_extra() -> None:
    service = FakeAgentService(total=2)
    director = make_director(service)

    async def drive() -> None:
        await director.start()
        for _ in range(5):
            director.note_candidate_speech("Answer.")
            await director.advance()

    asyncio.run(drive())

    assert director.is_finished
    assert len(service.answers) == 2


def test_follow_up_budget_counts_down_and_resets() -> None:
    director = make_director()

    async def drive() -> None:
        await director.start()
        assert director.follow_ups_remaining == 2
        director.note_follow_up_asked()
        assert director.follow_ups_remaining == 1
        director.note_follow_up_asked()
        assert director.follow_ups_remaining == 0
        director.note_candidate_speech("Answer.")
        await director.advance()
        assert director.follow_ups_remaining == 2

    asyncio.run(drive())


def test_abandoned_interview_is_ended_without_evaluating() -> None:
    service = FakeAgentService()
    director = make_director(service)

    async def drive() -> None:
        await director.start()
        await director.end_if_unfinished()

    asyncio.run(drive())

    assert service.ended is True


def test_completed_interview_is_not_ended_twice() -> None:
    service = FakeAgentService(total=1)
    director = make_director(service)

    async def drive() -> None:
        await director.start()
        director.note_candidate_speech("Answer.")
        await director.advance()
        await director.end_if_unfinished()

    asyncio.run(drive())

    assert director.is_finished
    assert service.ended is False


# ===========================================================================
# 4. Product contracts preserved, and V1 still available
# ===========================================================================


def test_dispatch_contract_is_unchanged() -> None:
    """Same agent name and same job metadata schema as the queue transport sends."""
    from services.livekit.agent_server import VoiceJobMetadata

    assert v2.VoiceJobMetadata is VoiceJobMetadata
    metadata = VoiceJobMetadata.model_validate(
        {"session_id": "s", "queue_item_id": 1, "candidate_id": 2, "position_id": 3}
    )
    assert metadata.session_id == "s"


def test_lifecycle_completion_contract_is_unchanged() -> None:
    """The React candidate page listens for exactly this topic and payload."""
    from services.livekit.agent_server import (
        INTERVIEW_COMPLETED_MESSAGE,
        INTERVIEW_LIFECYCLE_TOPIC,
    )

    assert v2.INTERVIEW_LIFECYCLE_TOPIC is INTERVIEW_LIFECYCLE_TOPIC
    assert v2.INTERVIEW_COMPLETED_MESSAGE is INTERVIEW_COMPLETED_MESSAGE
    assert INTERVIEW_LIFECYCLE_TOPIC == "hr.interview.lifecycle"


def test_opening_is_spoken_verbatim_not_generated() -> None:
    """SPEC 13 AI disclosure cannot be left to a language model's phrasing."""
    source = inspect.getsource(v2.InterviewVoiceAgentV2.on_enter)

    assert "session.say(" in source
    assert "allow_interruptions=False" in source
    # The disclosure must not be barge-overable before it has been made.
    assert "wait_for_playout" in source


def test_rendered_opening_still_discloses_ai() -> None:
    persona = default_persona("Aimy", "FlairsTech")
    rendered = v2.render_script(
        persona.opening_script, persona=persona, candidate_name="Omar Sayed"
    )

    assert "AI" in rendered
    assert "Omar" in rendered


def test_use_candidate_name_toggle_is_honoured() -> None:
    """A recruiter turning off name use must behave the same on V2."""
    persona = default_persona("Aimy", "FlairsTech")
    private = persona.model_copy(
        update={
            "conversational_style": persona.conversational_style.model_copy(
                update={"use_candidate_name": False}
            )
        }
    )

    rendered = v2.render_script(
        private.opening_script, persona=private, candidate_name="Omar Sayed"
    )

    assert "Omar" not in rendered
    assert "there" in rendered


def test_instructions_carry_the_compliance_rules() -> None:
    persona = default_persona("Aimy", "FlairsTech")
    instructions = v2.build_instructions(persona, "Omar Sayed", "Junior AI Engineer")

    assert "You are an AI" in instructions
    assert "emotion" in instructions
    assert "Never give feedback" in instructions
    assert "Never invent your own interview questions" in instructions


def test_briefing_carries_the_current_question_and_budget() -> None:
    view = QuestionView(
        question_id="q1",
        text="Tell me about your last project.",
        category="technical",
        index=1,
        total=4,
    )

    briefing = v2.question_briefing(view, follow_ups_left=2)
    assert "Tell me about your last project." in briefing
    assert "1 of 4" in briefing
    assert "2 more follow-up" in briefing

    exhausted = v2.question_briefing(view, follow_ups_left=0)
    assert "No follow-ups left" in exhausted


def test_finished_briefing_stops_further_questions() -> None:
    finished = QuestionView(
        question_id="", text="", category="", index=3, total=3, finished=True
    )

    briefing = v2.question_briefing(finished, follow_ups_left=0)
    assert "complete" in briefing.lower()
    assert "Do not ask anything further" in briefing


# ---------------------------------------------------------------------------
# Worker startup wiring
#
# Regression: build_server() originally fell off the end of the function without
# returning the AgentServer. Passing settings explicitly and passing nothing hit
# the same code path, so it was not a settings problem -- the function simply
# had no return. cli.run_app then received None and died on
# "'NoneType' object has no attribute 'log_level'" while reading the server's
# own log level, before it ever looked at the job handler.
# ---------------------------------------------------------------------------


def test_build_server_returns_a_real_agent_server() -> None:
    from livekit.agents import AgentServer

    server = v2.build_server()

    assert server is not None
    assert isinstance(server, AgentServer)


def test_build_server_returns_a_server_with_explicit_settings_too() -> None:
    """Both call paths must return; only the no-arg one was exercised at startup."""
    from livekit.agents import AgentServer

    server = v2.build_server(Settings())

    assert isinstance(server, AgentServer)


def test_built_server_exposes_the_log_level_run_app_reads() -> None:
    """The exact attribute whose absence produced the reported AttributeError."""
    server = v2.build_server()

    assert server.log_level is not None


def test_built_server_registers_the_job_handler_under_the_dispatch_name() -> None:
    """A returned-but-empty server would start and then never accept a job."""
    settings = Settings()
    server = v2.build_server(settings)

    assert server._agent_name == settings.livekit_agent_name
    assert server._entrypoint_fnc is not None
    assert callable(server._entrypoint_fnc)


def test_run_voice_agent_v2_hands_run_app_a_real_server(monkeypatch) -> None:
    """End of the wiring: what cli.run_app actually receives at startup.

    Asserting on build_server() alone would not have caught a run_voice_agent_v2
    that built a server and then passed something else.
    """
    from livekit.agents import AgentServer, cli

    received: dict[str, object] = {}

    def fake_run_app(server):
        received["server"] = server

    monkeypatch.setattr(cli, "run_app", fake_run_app)
    monkeypatch.setattr(v2.cli, "run_app", fake_run_app)

    v2.run_voice_agent_v2()

    assert "server" in received, "run_app was never called"
    assert received["server"] is not None
    assert isinstance(received["server"], AgentServer)


# ---------------------------------------------------------------------------
# Regression: Railway production startup died with
#   AttributeError: Can't get local object 'build_server.<locals>.voice_interview'
# The LiveKit worker runs jobs in subprocesses (multiprocessing, forkserver or
# spawn on Linux) and transfers the entrypoint by qualified name. A function
# nested inside build_server() has the qualname "build_server.<locals>
# .voice_interview", which pickle cannot resolve on the other side. Dev mode
# never hit it because it runs the job in-process.
# ---------------------------------------------------------------------------


def test_the_registered_entrypoint_is_pickleable() -> None:
    """What the production worker actually does to hand a job to a subprocess."""
    import pickle

    entrypoint = v2.build_server(Settings())._entrypoint_fnc

    restored = pickle.loads(pickle.dumps(entrypoint))
    assert restored is v2.voice_interview


def test_the_registered_entrypoint_is_module_level() -> None:
    """The precise property whose absence produced the production error."""
    entrypoint = v2.build_server(Settings())._entrypoint_fnc

    assert "<locals>" not in entrypoint.__qualname__
    assert entrypoint.__qualname__ == "voice_interview"
    assert entrypoint.__module__ == "services.livekit.v2.agent_server_v2"
    # Resolvable by name from the module, which is how pickle rebuilds it.
    assert getattr(v2, entrypoint.__qualname__) is entrypoint


def test_the_entrypoint_closes_over_nothing() -> None:
    """A closure variable is what made it unpickleable in the first place.

    Settings now come from _entrypoint_settings(), so a subprocess that
    re-imports this module resolves them from its inherited environment
    instead of needing the parent's captured value.
    """
    entrypoint = v2.build_server(Settings())._entrypoint_fnc

    assert entrypoint.__code__.co_freevars == ()
    assert entrypoint.__closure__ is None


def test_the_entrypoint_resolves_settings_without_build_server() -> None:
    """The spawn/forkserver case: the child never runs build_server()."""
    v2._configured_settings = None
    try:
        assert isinstance(v2._entrypoint_settings(), Settings)
    finally:
        v2._configured_settings = None


def test_build_server_settings_reach_the_entrypoint_in_process() -> None:
    """Dev mode and tests must still see the settings they passed in."""
    settings = Settings(livekit_agent_name="pickle-check-agent")
    v2.build_server(settings)

    assert v2._entrypoint_settings() is settings


def test_v1_entrypoint_also_still_returns_a_server() -> None:
    """The rollback path must not have the same defect."""
    from livekit.agents import AgentServer

    from services.livekit.agent_server import build_server as build_v1

    assert isinstance(build_v1(Settings()), AgentServer)


def test_v1_is_still_available_for_rollback() -> None:
    """V1 was not deleted; VOICE_ENGINE=v1 must still resolve to it."""
    from services.livekit.agent_server import (
        CoreInterviewVoiceAgent,
        run_voice_agent,
    )

    assert CoreInterviewVoiceAgent is not None
    assert callable(run_voice_agent)
    assert Settings(voice_engine="v1").voice_engine == "v1"


def test_voice_engine_defaults_to_v2_and_rejects_unknown() -> None:
    from pydantic import ValidationError

    assert Settings().voice_engine == "v2"
    with pytest.raises(ValidationError, match="voice_engine"):
        Settings(voice_engine="v3")


def test_entrypoint_selects_engine_from_settings() -> None:
    import voice_agent

    source = inspect.getsource(voice_agent)
    assert "voice_engine" in source
    assert "run_voice_agent_v2" in source
    assert "run_voice_agent" in source
