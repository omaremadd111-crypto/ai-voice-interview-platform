"""Phase 2: SilenceCoordinator escalation ladder, races R1/R2/R8, and the
no-response resolution contract (empty answer, never fabricated speech).

The coordinator is synchronous and clock-free: tests drive it with explicit
monotonic timestamps instead of sleeping.
"""
import json
import logging
from pathlib import Path

import pytest

from application.voice_silence_coordinator import (
    SilenceCoordinator,
    SilenceLadder,
    SilenceSuggestion,
)
from config.settings import Settings, load_settings


def build_ladder(**overrides) -> SilenceLadder:
    return SilenceLadder(**overrides)


def test_ladder_fires_each_escalation_once_in_order() -> None:
    # Cap raised above the three nudge rungs so the FULL ladder ordering is
    # observable; the shipped default cap of 2 has its own test below.
    coordinator = SilenceCoordinator(build_ladder(max_nudges_per_question=3))
    coordinator.arm_after_question("s1", "q1")

    assert coordinator.check("s1", 5.9) is None
    assert coordinator.check("s1", 6.0) is SilenceSuggestion.THINK_PAUSE
    # Same tick again: nothing new.
    assert coordinator.check("s1", 6.0) is None
    assert coordinator.check("s1", 14.9) is None
    assert coordinator.check("s1", 15.0) is SilenceSuggestion.PRESENCE_CHECK
    assert coordinator.check("s1", 24.9) is None
    assert coordinator.check("s1", 25.0) is SilenceSuggestion.PRESENCE_RETRY
    assert coordinator.check("s1", 44.9) is None
    assert coordinator.check("s1", 45.0) is SilenceSuggestion.NO_RESPONSE


def test_default_cap_of_two_nudges_stops_the_third_rung() -> None:
    """Shipped defaults: think-pause + presence-check exhaust the nagging budget;
    the retry rung stays silent while NO_RESPONSE still terminates the question."""
    coordinator = SilenceCoordinator(build_ladder())
    coordinator.arm_after_question("s1", "q1")

    assert coordinator.check("s1", 7.0) is SilenceSuggestion.THINK_PAUSE
    assert coordinator.check("s1", 16.0) is SilenceSuggestion.PRESENCE_CHECK
    assert coordinator.check("s1", 26.0) is None
    assert coordinator.check("s1", 46.0) is SilenceSuggestion.NO_RESPONSE


def test_nudge_cap_per_question_is_respected() -> None:
    coordinator = SilenceCoordinator(build_ladder(max_nudges_per_question=2))
    coordinator.arm_after_question("s1", "q1")

    assert coordinator.check("s1", 7.0) is SilenceSuggestion.THINK_PAUSE
    assert coordinator.check("s1", 16.0) is SilenceSuggestion.PRESENCE_CHECK
    # Cap reached: the presence retry must not fire even though its threshold passed.
    assert coordinator.check("s1", 26.0) is None
    assert coordinator.nudge_count("s1") == 2


def test_r1_candidate_speech_before_threshold_cancels_the_pending_nudge() -> None:
    coordinator = SilenceCoordinator(build_ladder())
    coordinator.arm_after_question("s1", "q1")
    coordinator.check("s1", 3.0)

    # Candidate starts speaking just before think_pause would fire...
    coordinator.on_user_state_changed("s1", "speaking")
    coordinator.check("s1", 8.0)
    assert coordinator.check("s1", 8.0) is None

    # ...and finishes: quiet time restarts from zero, no stale nudge.
    coordinator.on_user_state_changed("s1", "listening")
    coordinator.note_conversation_activity("s1")
    assert coordinator.check("s1", 10.0) is None
    assert coordinator.check("s1", 16.0) is SilenceSuggestion.THINK_PAUSE


def test_r2_agent_thinking_and_speaking_never_count_as_candidate_silence() -> None:
    """THE critical Phase 2 constraint: classifier/TTS latency is system time."""
    coordinator = SilenceCoordinator(build_ladder())
    coordinator.arm_after_question("s1", "q1")
    coordinator.check("s1", 0.0)

    # Classifier call in flight for 4 seconds (agent thinking), then TTS speaks
    # for another 3 -- together longer than think_pause itself.
    coordinator.on_agent_state_changed("s1", "thinking")
    coordinator.check("s1", 4.0)
    coordinator.on_agent_state_changed("s1", "speaking")
    coordinator.check("s1", 7.0)
    coordinator.on_agent_state_changed("s1", "listening")
    assert coordinator.check("s1", 7.0) is None

    # Only ~0.5s of real candidate quiet has elapsed since listening resumed.
    assert coordinator.check("s1", 7.5) is None
    assert coordinator.check("s1", 13.5) is SilenceSuggestion.THINK_PAUSE


def test_r2_partial_quiet_before_busy_state_is_preserved_not_doubled() -> None:
    coordinator = SilenceCoordinator(build_ladder())
    coordinator.arm_after_question("s1", "q1")
    coordinator.check("s1", 2.0)

    coordinator.on_user_state_changed("s1", "speaking")
    coordinator.check("s1", 5.0)   # candidate speaking: no accumulation
    coordinator.on_user_state_changed("s1", "listening")
    coordinator.check("s1", 5.5)   # 2.0 + 0.5 = 2.5s quiet total

    coordinator.note_conversation_activity("s1")  # Aimy spoke; clock restarts
    assert coordinator.check("s1", 11.5) is SilenceSuggestion.THINK_PAUSE


# ---- Batch 1: on_user_state_changed resume-path fix -----------------------
#
# Confirmed production bug (real session log, ~24s silent gap with zero
# nudges): on_user_state_changed only ever PAUSED the clock. The only resume
# paths were an agent state transition or a delivered reply -- so a candidate
# utterance that never produced either (STT/turn detection failed to
# finalize it into a turn) left the clock paused forever, even though the
# candidate then went genuinely silent. These tests reproduce that exact
# shape directly against the coordinator, with no agent action anywhere.


def test_user_speech_blip_with_no_agent_action_still_resumes_the_clock() -> None:
    """The core bug fix: stopping speech resumes the clock even when nothing
    else ever happens afterward -- no arm_after_question, no
    note_conversation_activity, no agent state change."""
    coordinator = SilenceCoordinator(build_ladder())
    coordinator.arm_after_question("s1", "q1")
    coordinator.check("s1", 0.0)

    coordinator.on_user_state_changed("s1", "speaking")
    coordinator.check("s1", 2.0)
    coordinator.on_user_state_changed("s1", "listening")
    # No agent action of any kind follows -- exactly the confirmed failure mode.
    assert coordinator.check("s1", 8.0) is SilenceSuggestion.THINK_PAUSE


def test_stopping_speech_does_not_resume_the_clock_while_agent_is_busy() -> None:
    """R2 still owns the busy case: if the candidate's blip ends while Aimy is
    mid-reply (thinking/speaking), the clock stays paused until the AGENT
    goes non-busy, not the instant the candidate goes quiet."""
    coordinator = SilenceCoordinator(build_ladder())
    coordinator.arm_after_question("s1", "q1")
    coordinator.check("s1", 0.0)

    coordinator.on_agent_state_changed("s1", "thinking")
    coordinator.on_user_state_changed("s1", "speaking")
    coordinator.check("s1", 2.0)
    coordinator.on_user_state_changed("s1", "listening")  # user quiet, agent still busy
    assert coordinator.check("s1", 8.0) is None

    coordinator.on_agent_state_changed("s1", "listening")  # agent finally free
    assert coordinator.check("s1", 8.0) is None
    assert coordinator.check("s1", 14.0) is SilenceSuggestion.THINK_PAUSE


def test_multiple_speech_blips_with_no_agent_action_accumulate_quiet_time_between_them() -> None:
    """Closer to the real session shape: several short blips in a row, none
    producing any agent action, with quiet time correctly accumulating
    ACROSS the gaps between them rather than only after the last one."""
    coordinator = SilenceCoordinator(build_ladder())
    coordinator.arm_after_question("s1", "q1")
    coordinator.check("s1", 0.0)

    coordinator.on_user_state_changed("s1", "speaking")
    coordinator.check("s1", 1.0)
    coordinator.on_user_state_changed("s1", "listening")
    assert coordinator.check("s1", 3.0) is None  # 2.0s quiet accumulated

    coordinator.on_user_state_changed("s1", "speaking")
    coordinator.check("s1", 3.5)
    coordinator.on_user_state_changed("s1", "listening")
    assert coordinator.check("s1", 5.0) is None  # 2.0 + 1.5 = 3.5s quiet total

    assert coordinator.check("s1", 8.0) is SilenceSuggestion.THINK_PAUSE  # 3.5 + 3.0 = 6.5s


def test_new_question_resets_nudge_counters_but_a_restatement_does_not() -> None:
    coordinator = SilenceCoordinator(build_ladder(max_nudges_per_question=1))
    coordinator.arm_after_question("s1", "q1")
    assert coordinator.check("s1", 7.0) is SilenceSuggestion.THINK_PAUSE
    assert coordinator.check("s1", 16.0) is None  # cap of 1 already used

    # Presence retry restates q1: counters survive so the cap still holds.
    coordinator.arm_after_question("s1", "q1")
    assert coordinator.nudge_count("s1") == 1
    assert coordinator.check("s1", 23.0) is None

    # The engine advances to q2: fresh question, fresh nudge budget.
    coordinator.arm_after_question("s1", "q2")
    assert coordinator.nudge_count("s1") == 0
    assert coordinator.check("s1", 30.0) is SilenceSuggestion.THINK_PAUSE


def test_r8_no_suggestions_after_dispose() -> None:
    coordinator = SilenceCoordinator(build_ladder())
    coordinator.arm_after_question("s1", "q1")
    coordinator.dispose_session("s1")

    assert coordinator.check("s1", 100.0) is None
    assert coordinator.check("s1", 200.0) is None


def test_no_response_is_terminal_until_the_next_question_rearms() -> None:
    coordinator = SilenceCoordinator(build_ladder())
    coordinator.arm_after_question("s1", "q1")

    first = coordinator.check("s1", 50.0)
    assert first is SilenceSuggestion.NO_RESPONSE
    # Every subsequent tick stays silent while the conversation layer resolves.
    assert coordinator.check("s1", 51.0) is None
    assert coordinator.check("s1", 90.0) is None

    coordinator.arm_after_question("s1", "q2")
    assert coordinator.check("s1", 96.0) is SilenceSuggestion.THINK_PAUSE


def test_sessions_never_interfere() -> None:
    coordinator = SilenceCoordinator(build_ladder())
    coordinator.arm_after_question("alpha", "q1")
    coordinator.arm_after_question("beta", "q1")

    coordinator.dispose_session("beta")
    assert coordinator.check("beta", 100.0) is None
    # Alpha's ladder is untouched by beta's disposal.
    assert coordinator.check("alpha", 100.0) is SilenceSuggestion.NO_RESPONSE


def test_unarmed_session_never_suggests() -> None:
    coordinator = SilenceCoordinator(build_ladder())
    assert coordinator.check("ghost", 999.0) is None


def test_arm_anchors_the_clock_to_the_caller_monotonic_timeline() -> None:
    """Regression: without explicit anchoring, the first check after arming
    accumulated the whole process uptime (monotonic() minus zero) as silence
    and fired NO_RESPONSE instantly."""
    coordinator = SilenceCoordinator(build_ladder())
    process_uptime = 987_654.0  # what time.monotonic() looks like in production

    coordinator.arm_after_question("s1", "q1", now_seconds=process_uptime)

    assert coordinator.check("s1", process_uptime + 0.5) is None
    assert coordinator.check("s1", process_uptime + 6.5) is SilenceSuggestion.THINK_PAUSE


# ---- Settings integration -----------------------------------------------------


def test_settings_expose_env_overridable_silence_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_environ,
) -> None:
    for key in (
        "VOICE_SILENCE_LAYER_ENABLED", "VOICE_THINK_PAUSE_SECONDS",
        "VOICE_PRESENCE_CHECK_SECONDS", "VOICE_PRESENCE_RETRY_SECONDS",
        "VOICE_NO_RESPONSE_SECONDS", "VOICE_MAX_NUDGES_PER_QUESTION",
        "VOICE_SILENCE_POLL_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = load_settings(env_file=Path("does-not-exist.env"))
    assert settings.voice_silence_layer_enabled is True
    assert settings.voice_think_pause_seconds == 6.0
    assert settings.voice_presence_check_seconds == 15.0
    assert settings.voice_presence_retry_seconds == 25.0
    assert settings.voice_no_response_seconds == 45.0
    assert settings.voice_max_nudges_per_question == 2
    assert settings.voice_silence_poll_interval_seconds == 0.5

    env_path = tmp_path / ".env"
    env_path.write_text(
        "VOICE_THINK_PAUSE_SECONDS=8.5\n"
        "VOICE_MAX_NUDGES_PER_QUESTION=3\n",
        encoding="utf-8",
    )
    loaded = load_settings(env_file=env_path)
    assert loaded.voice_think_pause_seconds == 8.5
    assert loaded.voice_max_nudges_per_question == 3


def test_settings_reject_non_increasing_silence_ladder() -> None:
    with pytest.raises(ValueError):
        Settings(voice_think_pause_seconds=20.0, voice_presence_check_seconds=15.0)
    with pytest.raises(ValueError):
        Settings(voice_presence_check_seconds=30.0, voice_presence_retry_seconds=25.0)


def test_settings_require_think_pause_above_stt_max_delay() -> None:
    # STT endpointing max_delay is 3.0s: a nudge at or below it could talk over
    # a real answer that endpointing is still resolving (R1).
    with pytest.raises(ValueError):
        Settings(voice_think_pause_seconds=3.0)
    with pytest.raises(ValueError):
        Settings(voice_think_pause_seconds=2.0)


def test_settings_reject_invalid_silence_values() -> None:
    with pytest.raises(ValueError):
        Settings(voice_no_response_seconds=0)
    with pytest.raises(ValueError):
        Settings(voice_max_nudges_per_question=0)
    with pytest.raises(ValueError):
        Settings(voice_silence_poll_interval_seconds=-1)


def test_build_silence_coordinator_reads_timings_from_settings_only() -> None:
    from application.voice_silence_coordinator import build_silence_coordinator

    settings = Settings(
        voice_think_pause_seconds=7.5,
        voice_presence_check_seconds=16.0,
        voice_presence_retry_seconds=27.0,
        voice_no_response_seconds=48.0,
        voice_max_nudges_per_question=3,
    )
    coordinator = build_silence_coordinator(settings)
    coordinator.arm_after_question("s1", "q1")
    assert coordinator.check("s1", 7.4) is None
    assert coordinator.check("s1", 7.5) is SilenceSuggestion.THINK_PAUSE
    assert coordinator.check("s1", 16.0) is SilenceSuggestion.PRESENCE_CHECK
    assert coordinator.check("s1", 27.0) is SilenceSuggestion.PRESENCE_RETRY


# ---- structured event ---------------------------------------------------------


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def captured_logs():
    logger = logging.getLogger("interview_agent")
    previous_level = logger.level
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _events(handler: _ListHandler) -> list[dict]:
    return [json.loads(message) for message in handler.messages]


def test_voice_intent_events_vocabulary_includes_voice_no_response() -> None:
    from services.logging_service import Event

    assert Event.VOICE_NO_RESPONSE.value == "VOICE_NO_RESPONSE"
