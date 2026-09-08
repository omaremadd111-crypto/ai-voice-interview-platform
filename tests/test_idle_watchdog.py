"""Regression tests for the candidate idle watchdog.

Uses tiny real delays (milliseconds) rather than a fake clock, so the tests
exercise the actual asyncio task scheduling, cancellation and post-sleep
re-validation that the race protections depend on. A fake clock would step over
exactly the interleavings these tests exist to pin down.

The FakeSession stands in for AgentSession: it records say() calls, exposes the
two state properties the watchdog re-reads after sleeping, and lets a test emit
the same events the SDK emits.
"""

from __future__ import annotations

import asyncio

import pytest

from config.settings import Settings
from services.livekit.v2.idle_watchdog import (
    FIRST_NUDGE,
    MAX_NUDGES_PER_QUESTION,
    SECOND_NUDGE,
    CandidateIdleWatchdog,
)

# Fast enough to keep the suite quick, slow enough that an await between
# emissions reliably lands inside the window.
FIRST = 0.05
SECOND = 0.05
# Comfortably longer than a timer, so "did it fire?" is unambiguous.
SETTLE = 0.15


class FakeStateEvent:
    def __init__(self, new_state: str, old_state: str | None = None) -> None:
        self.new_state = new_state
        self.old_state = old_state


class FakeSession:
    """Minimal AgentSession stand-in: .on(), .say(), and the two state props."""

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}
        self.said: list[str] = []
        self.agent_state = "initializing"
        self.user_state = "listening"

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def say(self, text: str, **kwargs):
        self.said.append(text)
        return None

    # --- helpers that mirror what the SDK does -------------------------------

    def emit(self, event: str, payload) -> None:
        for handler in self.handlers.get(event, []):
            handler(payload)

    def agent_becomes(self, state: str) -> None:
        old, self.agent_state = self.agent_state, state
        self.emit("agent_state_changed", FakeStateEvent(state, old))

    def user_becomes(self, state: str) -> None:
        old, self.user_state = self.user_state, state
        self.emit("user_state_changed", FakeStateEvent(state, old))

    def finish_speaking(self) -> None:
        """The real sequence: agent speaks, then returns to listening."""
        self.agent_becomes("speaking")
        self.agent_becomes("listening")


def make_watchdog(
    session: FakeSession,
    *,
    first: float = FIRST,
    second: float = SECOND,
    is_finished=None,
) -> CandidateIdleWatchdog:
    watchdog = CandidateIdleWatchdog(
        session,
        first_nudge_seconds=first,
        second_nudge_seconds=second,
        is_finished=is_finished,
    )
    watchdog.attach()
    return watchdog


# ===========================================================================
# Core behaviour
# ===========================================================================


def test_silence_after_a_question_triggers_the_first_nudge() -> None:
    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == [FIRST_NUDGE]


def test_continued_silence_triggers_the_second_nudge() -> None:
    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        # The nudge itself is agent speech; its speaking -> listening
        # transition is what re-arms the watchdog for the second tier.
        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == [FIRST_NUDGE, SECOND_NUDGE]


def test_never_more_than_two_nudges_per_unanswered_question() -> None:
    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        # Five opportunities to nudge; the budget must cap it at two.
        for _ in range(5):
            session.finish_speaking()
            await asyncio.sleep(SETTLE)
        return session.said

    said = asyncio.run(scenario())
    assert said == [FIRST_NUDGE, SECOND_NUDGE]
    assert len(said) == MAX_NUDGES_PER_QUESTION


def test_nudges_are_deterministic_text_and_never_generated() -> None:
    """No LLM in the path: the strings are module constants."""
    assert FIRST_NUDGE == "Take your time."
    assert SECOND_NUDGE == "Would you like me to repeat the question?"


# ===========================================================================
# Cancellation
# ===========================================================================


def test_candidate_speech_cancels_the_watchdog() -> None:
    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        session.finish_speaking()
        # Speak well before the timer would fire.
        await asyncio.sleep(FIRST / 5)
        session.user_becomes("speaking")
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


def test_no_nudge_fires_while_the_agent_is_speaking() -> None:
    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        session.finish_speaking()
        await asyncio.sleep(FIRST / 5)
        # The agent starts speaking again (e.g. a follow-up).
        session.agent_becomes("speaking")
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


def test_no_nudge_fires_during_active_candidate_speech() -> None:
    """Even if a timer is somehow pending, a speaking candidate blocks it.

    Guards the post-sleep re-validation specifically: the timer is armed, the
    generation is left untouched, and only session.user_state says the
    candidate is talking.
    """

    async def scenario():
        session = FakeSession()
        watchdog = make_watchdog(session)

        session.finish_speaking()
        # Mutate the state the watchdog re-reads after sleeping, without
        # emitting the event that would cancel the timer.
        session.user_state = "speaking"
        await asyncio.sleep(SETTLE)
        assert watchdog is not None
        return session.said

    assert asyncio.run(scenario()) == []


def test_no_nudge_during_tool_execution_or_generation() -> None:
    """The SDK reports both LLM generation and tool execution as "thinking"."""

    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        session.finish_speaking()
        await asyncio.sleep(FIRST / 5)
        session.agent_becomes("thinking")
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


def test_mid_answer_pause_does_not_arm_the_watchdog() -> None:
    """A candidate pausing mid-answer must not be nudged.

    The candidate speaks, then pauses -- user_state returns to "listening"
    without the agent having spoken. Arming happens only on the AGENT's
    speaking -> listening transition, so no timer exists to fire.
    """

    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        session.finish_speaking()
        session.user_becomes("speaking")
        # The pause: candidate stops, but the agent has not responded yet.
        session.user_becomes("listening")
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


# ===========================================================================
# Race protection
# ===========================================================================


def test_stale_timer_from_question_a_cannot_fire_during_question_b() -> None:
    async def scenario():
        session = FakeSession()
        watchdog = make_watchdog(session)

        session.finish_speaking()  # arms under question A
        await asyncio.sleep(FIRST / 5)
        watchdog.notify_question_changed()  # question B begins
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


def test_a_timer_already_past_its_sleep_cannot_speak_for_an_ended_question() -> None:
    """The generation guard, tested directly at the one moment it matters.

    Cancellation alone cannot protect this: a timer that has already resumed
    past its await is beyond cancelling, so the decision to speak has to be
    re-validated against the CURRENT generation. Driving _wait_then_nudge with
    a stale generation reproduces exactly that interleaving, which ordinary
    event sequencing cannot schedule deterministically.
    """

    async def scenario():
        session = FakeSession()
        watchdog = make_watchdog(session)
        session.agent_state = "listening"

        stale_generation = watchdog._generation
        watchdog.notify_question_changed()  # question B begins

        # A timer scheduled under question A, waking up now.
        await watchdog._wait_then_nudge(0.0, stale_generation)
        return session.said

    assert asyncio.run(scenario()) == []


def test_a_timer_past_its_sleep_still_speaks_for_the_current_question() -> None:
    """The counterpart: the guard must not suppress a legitimate nudge."""

    async def scenario():
        session = FakeSession()
        watchdog = make_watchdog(session)
        session.agent_state = "listening"

        await watchdog._wait_then_nudge(0.0, watchdog._generation)
        return session.said

    assert asyncio.run(scenario()) == [FIRST_NUDGE]


def test_a_timer_already_past_its_sleep_cannot_speak_over_a_talking_candidate() -> None:
    """Same interleaving, but the candidate started talking instead."""

    async def scenario():
        session = FakeSession()
        watchdog = make_watchdog(session)
        session.agent_state = "listening"
        generation = watchdog._generation

        session.user_state = "speaking"
        await watchdog._wait_then_nudge(0.0, generation)
        return session.said

    assert asyncio.run(scenario()) == []


def test_a_new_question_restores_the_full_nudge_budget() -> None:
    async def scenario():
        session = FakeSession()
        watchdog = make_watchdog(session)

        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        assert session.said == [FIRST_NUDGE, SECOND_NUDGE]

        watchdog.notify_question_changed()
        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        return session.said

    # Budget reset, so the new question starts again at the first nudge.
    assert asyncio.run(scenario()) == [FIRST_NUDGE, SECOND_NUDGE, FIRST_NUDGE]


def test_candidate_speech_restores_the_nudge_budget() -> None:
    """An answered question ends the idle episode; the next one starts fresh."""

    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        assert session.said == [FIRST_NUDGE]

        session.user_becomes("speaking")
        session.user_becomes("listening")
        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == [FIRST_NUDGE, FIRST_NUDGE]


def test_only_one_timer_is_pending_per_question() -> None:
    """Repeated arming replaces rather than accumulates timers.

    Without replacement, three arms would queue three nudges and blow the
    budget in a single window.
    """

    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        session.finish_speaking()
        session.finish_speaking()
        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == [FIRST_NUDGE]


def test_close_cancels_a_pending_timer() -> None:
    async def scenario():
        session = FakeSession()
        watchdog = make_watchdog(session)

        session.finish_speaking()
        await asyncio.sleep(FIRST / 5)
        watchdog.close()
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


def test_session_close_event_cancels_pending_timers() -> None:
    """Shutdown via the SDK's own close event, not an explicit call."""

    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        session.finish_speaking()
        await asyncio.sleep(FIRST / 5)
        session.emit("close", object())
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


def test_aclose_awaits_the_timer_and_silences_it() -> None:
    async def scenario():
        session = FakeSession()
        watchdog = make_watchdog(session)

        session.finish_speaking()
        await asyncio.sleep(FIRST / 5)
        await watchdog.aclose()
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


def test_a_closed_watchdog_never_arms_again() -> None:
    async def scenario():
        session = FakeSession()
        watchdog = make_watchdog(session)

        watchdog.close()
        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


def test_no_nudge_once_the_interview_is_finished() -> None:
    """Interview ending / shutdown must be silent."""

    async def scenario():
        finished = {"value": False}
        session = FakeSession()
        make_watchdog(session, is_finished=lambda: finished["value"])

        finished["value"] = True
        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


def test_interview_finishing_mid_timer_suppresses_the_nudge() -> None:
    """The finished check is re-made after the sleep, not only at arm time."""

    async def scenario():
        finished = {"value": False}
        session = FakeSession()
        make_watchdog(session, is_finished=lambda: finished["value"])

        session.finish_speaking()
        await asyncio.sleep(FIRST / 5)
        finished["value"] = True  # e.g. the closing began
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == []


def test_away_state_neither_arms_nor_cancels() -> None:
    """The SDK's own 15s away marker must not disturb the watchdog."""

    async def scenario():
        session = FakeSession()
        make_watchdog(session)

        session.finish_speaking()
        session.user_becomes("away")
        await asyncio.sleep(SETTLE)
        return session.said

    # Still nudges: "away" is not candidate speech, so nothing was cancelled.
    assert asyncio.run(scenario()) == [FIRST_NUDGE]


# ===========================================================================
# Configuration + isolation from the stable pipeline
# ===========================================================================


def test_default_timings_match_the_specified_behaviour() -> None:
    settings = Settings()
    assert settings.voice_idle_nudge_enabled is True
    assert settings.voice_idle_first_nudge_seconds == 8.0
    # Second nudge specified as 8-10 seconds after the first.
    assert 8.0 <= settings.voice_idle_second_nudge_seconds <= 10.0


def _imported_names(module) -> set[str]:
    """Every module and symbol a module actually imports.

    Parsed from the AST rather than grepped from source text: the watchdog's
    own docstring explains at length what it deliberately does NOT touch
    ("The problem this solves is NOT endpointing..."), so a text search matches
    the prose that documents the exclusion. Only a real import counts.
    """
    import ast
    import inspect

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


def test_watchdog_does_not_import_any_tuned_component() -> None:
    """It must not import or configure anything in the stable pipeline."""
    from services.livekit.v2 import idle_watchdog

    imported = _imported_names(idle_watchdog)
    for forbidden in (
        "services.livekit.v2.turn_tuning",
        "build_turn_handling",
        "build_vad",
        "services.livekit.v2.session",
        "build_session",
        "inference",
    ):
        assert forbidden not in imported, f"watchdog imports {forbidden}"


def test_watchdog_makes_no_llm_call() -> None:
    """say() is direct TTS of a constant; generate_reply would be the LLM path."""
    import inspect

    from services.livekit.v2.idle_watchdog import CandidateIdleWatchdog

    speaking_path = inspect.getsource(CandidateIdleWatchdog._wait_then_nudge)
    assert "generate_reply" not in speaking_path
    assert "self._session.say(" in speaking_path


def test_old_silence_coordinator_is_not_reused() -> None:
    from services.livekit.v2 import agent_server_v2, idle_watchdog

    for module in (idle_watchdog, agent_server_v2):
        imported = _imported_names(module)
        assert "application.voice_silence_coordinator" not in imported
        assert "SilenceCoordinator" not in imported
        assert "build_silence_coordinator" not in imported


def test_voice_v2_tuning_is_unchanged() -> None:
    """The stable configuration, re-pinned after adding the watchdog."""
    from services.livekit.v2.turn_tuning import build_turn_handling

    th = build_turn_handling(Settings())
    assert th["endpointing"] == {
        "mode": "dynamic",
        "min_delay": 0.4,
        "max_delay": 2.0,
        "alpha": 0.9,
    }
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


def test_disabling_the_watchdog_leaves_the_session_untouched() -> None:
    """VOICE_IDLE_NUDGE_ENABLED=false must attach nothing at all.

    Read from the entrypoint the server actually registers rather than a
    hardcoded function name: the per-job wiring moved out of build_server()
    when the entrypoint was hoisted to module level to make it pickleable for
    the production worker, and this assertion should follow the code rather
    than have to be rewritten each time it moves.
    """
    import inspect

    from config.settings import Settings
    from services.livekit.v2 import agent_server_v2

    entrypoint = agent_server_v2.build_server(Settings())._entrypoint_fnc
    source = inspect.getsource(entrypoint)
    assert "if resolved.voice_idle_nudge_enabled:" in source
    assert "idle_watchdog: CandidateIdleWatchdog | None = None" in source
