"""Silence / presence coordinator for voice sessions (conversational-quality Phase 2).

Distinguishes the four states that all used to look identical (nothing arriving):

    thinking            quiet, candidate still present        -> "Take your time."
    disengaged          longer quiet, no speech events         -> "Are you still with me?"
    connection trouble  LiveKit state/track signals            -> presence retry / restate
    answer complete     endpointing delivered a transcript     -> normal turn processing

Design constraints:

- This module EMITS SUGGESTIONS ONLY. It never calls InterviewEngine or
  InterviewAgentService, never advances an interview, and never fabricates
  candidate speech. The conversation layer decides what a suggestion means.
- R1: every threshold must sit above STT max_delay (3.0s) -- enforced in
  Settings -- and any candidate speech event immediately pauses the clock.
- R2 (the critical one): classifier/LLM/TTS latency is SYSTEM time, not
  candidate silence. The clock only accumulates while the agent state is
  listening/idle AND the user is not speaking; `thinking`/`speaking` pause it.
- R8: once disposed (interview completed or participant disconnected), the
  coordinator is inert forever -- no suggestion can fire after the interview.
- R10: every piece of state is keyed by session_id; one instance safely serves
  many concurrent sessions.

The coordinator itself is deliberately synchronous and clock-free: callers
advance it with their own monotonic timestamps (`check(session_id, now)`), so
the LiveKit agent process drives it from an asyncio tick while tests drive it
with fake clocks. No threads, no timers, no awaits in here.
"""
from dataclasses import dataclass, field
from enum import StrEnum

from config.settings import Settings

#: LiveKit AgentStates during which the system -- not the candidate -- owns the
#: timeline. While the agent is thinking (e.g. a classifier call in flight) or
#: speaking (TTS playout), silence must NOT accumulate against the candidate.
_AGENT_BUSY_STATES = frozenset({"thinking", "speaking"})

#: UserStates emitted by the LiveKit SDK. `speaking` means candidate audio is
#: being captured; everything else counts as quiet.
_USER_SPEAKING_STATE = "speaking"


class SilenceSuggestion(StrEnum):
    """What the conversation layer should do about the current quiet."""

    THINK_PAUSE = "think_pause"          # gentle "Take your time." (once per question)
    PRESENCE_CHECK = "presence_check"    # "Are you still with me?"
    PRESENCE_RETRY = "presence_retry"    # restate the current question
    NO_RESPONSE = "no_response"          # ladder exhausted: resolve the question


@dataclass(frozen=True)
class SilenceLadder:
    """Timing thresholds, in strictly increasing seconds. Built from Settings."""

    think_pause_seconds: float = 6.0
    presence_check_seconds: float = 15.0
    presence_retry_seconds: float = 25.0
    no_response_seconds: float = 45.0
    max_nudges_per_question: int = 2

    def __post_init__(self) -> None:
        values = (
            self.think_pause_seconds,
            self.presence_check_seconds,
            self.presence_retry_seconds,
            self.no_response_seconds,
        )
        if any(value <= 0 for value in values):
            raise ValueError("silence ladder timings must be positive")
        if any(later <= earlier for earlier, later in zip(values, values[1:])):
            raise ValueError("silence ladder timings must be strictly increasing")
        if self.max_nudges_per_question < 1:
            raise ValueError("max_nudges_per_question must be at least 1")


@dataclass
class _SessionSilenceState:
    """Per-session quiet clock and per-question nudge bookkeeping.

    `quiet_seconds` accumulates ONLY while running and neither side is active --
    never across agent thinking/speaking stretches (R2) and never after dispose
    (R8). Nudge counters reset when the conversation layer reports that a new
    question has been asked.
    """

    quiet_seconds: float = 0.0
    running: bool = False
    disposed: bool = False
    nudges_this_question: int = 0
    think_pause_given: bool = False
    last_seen_question_id: str | None = None
    user_speaking: bool = False
    agent_busy: bool = False
    last_check_at: float = 0.0
    history: list[tuple[float, SilenceSuggestion]] = field(default_factory=list)


class SilenceCoordinator:
    """Owns presence/quiet timing for voice sessions. Emits suggestions;
    never calls the interview core; never fabricates speech."""

    def __init__(self, ladder: SilenceLadder) -> None:
        self._ladder = ladder
        self._sessions: dict[str, _SessionSilenceState] = {}

    # ---- lifecycle -------------------------------------------------------

    def arm_after_question(
        self, session_id: str, question_id: str | None = None,
        *, now_seconds: float | None = None,
    ) -> None:
        """Start (or restart) the quiet watch for the question just asked.

        Called by the agent layer whenever Aimy finishes speaking a question to
        the candidate. A different question_id resets the per-question nudge
        counters; repeating the same question (a presence retry restating it)
        keeps them so the ladder cannot nag past its cap by re-asking.

        ``now_seconds`` anchors the quiet clock to the caller's monotonic
        timeline; passing it on every arm keeps the first real check from
        accumulating the entire process uptime as silence.
        """
        state = self._state_for(session_id)
        if question_id is not None and question_id != state.last_seen_question_id:
            state.nudges_this_question = 0
            state.think_pause_given = False
            # Fresh question: every rung becomes eligible again.
            state.history.clear()
            state.last_seen_question_id = question_id
        state.quiet_seconds = 0.0
        if now_seconds is not None:
            state.last_check_at = now_seconds
        state.running = True

    def note_conversation_activity(
        self, session_id: str, *, now_seconds: float | None = None,
    ) -> None:
        """Any meaningful spoken exchange happened: restart the quiet clock."""
        state = self._state_for(session_id)
        state.quiet_seconds = 0.0
        state.running = True
        if now_seconds is not None:
            state.last_check_at = now_seconds

    def on_user_state_changed(self, session_id: str, new_state: str) -> None:
        """R1: the moment candidate audio starts, silence stops accumulating.

        Symmetric resume: the moment the candidate stops speaking again, the
        clock resumes counting real quiet time -- unless the agent is busy
        (R2 owns that case; it will resume the clock itself when it stops
        being busy). Before this fix, this method only ever paused the
        clock -- the only resume paths were an AGENT state transition or a
        delivered reply (``arm_after_question``/``note_conversation_activity``),
        so a candidate utterance that never produced either (e.g. STT/turn
        detection failed to finalize it into a turn) left the clock paused
        indefinitely even though the candidate then went genuinely silent.
        Confirmed against a real session log: a ~24s silent gap with the
        agent never acting produced zero nudges for exactly this reason.
        """
        state = self._state_for(session_id)
        was_speaking = state.user_speaking
        state.user_speaking = new_state == _USER_SPEAKING_STATE
        if state.user_speaking and not was_speaking:
            self._pause(state)
        elif was_speaking and not state.user_speaking and not state.agent_busy:
            state.running = True

    def on_agent_state_changed(self, session_id: str, new_state: str) -> None:
        """R2: agent thinking/speaking is system latency, never candidate silence."""
        state = self._state_for(session_id)
        busy = new_state in _AGENT_BUSY_STATES
        if busy == state.agent_busy:
            return
        state.agent_busy = busy
        if busy:
            self._pause(state)
        elif not state.user_speaking:
            state.running = True

    def dispose_session(self, session_id: str) -> None:
        """R8: completion or disconnect makes every future check a no-op."""
        state = self._state_for(session_id)
        state.disposed = True
        state.running = False

    # ---- observation -----------------------------------------------------

    def check(self, session_id: str, now_seconds: float) -> SilenceSuggestion | None:
        """Advance the quiet clock to ``now_seconds`` and return the highest
        escalation currently due, or None.

        ``now_seconds`` is the caller's monotonic timestamp; the delta since the
        previous check accumulates only while the clock is running and neither
        side is active. Suggestions are returned at most once each (except
        NO_RESPONSE, which is terminal for the question); the nudge cap bounds
        THINK_PAUSE + PRESENCE_CHECK + PRESENCE_RETRY combined per question.
        """
        state = self._sessions.get(session_id)
        if state is None or state.disposed:
            return None

        elapsed = max(0.0, now_seconds - state.last_check_at)
        state.last_check_at = now_seconds
        if elapsed > 0 and state.running and not (state.agent_busy or state.user_speaking):
            state.quiet_seconds += elapsed

        quiet = state.quiet_seconds
        already_emitted = {suggestion for _, suggestion in state.history}

        if SilenceSuggestion.NO_RESPONSE in already_emitted:
            # Terminal for this question; stay silent until the next question
            # re-arms the ladder.
            return None
        if quiet >= self._ladder.no_response_seconds:
            return self._record(state, now_seconds, SilenceSuggestion.NO_RESPONSE)
        if (
            quiet >= self._ladder.presence_retry_seconds
            and SilenceSuggestion.PRESENCE_RETRY not in already_emitted
            and state.nudges_this_question < self._ladder.max_nudges_per_question
        ):
            return self._record(state, now_seconds, SilenceSuggestion.PRESENCE_RETRY)
        if (
            quiet >= self._ladder.presence_check_seconds
            and SilenceSuggestion.PRESENCE_CHECK not in already_emitted
            and state.nudges_this_question < self._ladder.max_nudges_per_question
        ):
            return self._record(state, now_seconds, SilenceSuggestion.PRESENCE_CHECK)
        if (
            quiet >= self._ladder.think_pause_seconds
            and not state.think_pause_given
            and state.nudges_this_question < self._ladder.max_nudges_per_question
        ):
            state.think_pause_given = True
            return self._record(state, now_seconds, SilenceSuggestion.THINK_PAUSE)
        return None

    def seconds_since_activity(self, session_id: str) -> float:
        """Quiet seconds accumulated for the session -- observability only."""
        state = self._sessions.get(session_id)
        if state is None:
            return 0.0
        return round(state.quiet_seconds, 3)

    def nudge_count(self, session_id: str) -> int:
        state = self._sessions.get(session_id)
        return state.nudges_this_question if state is not None else 0

    # ---- internals -------------------------------------------------------

    def _pause(self, state: _SessionSilenceState) -> None:
        state.running = False

    def _record(
        self, state: _SessionSilenceState, now_seconds: float, suggestion: SilenceSuggestion,
    ) -> SilenceSuggestion:
        state.history.append((now_seconds, suggestion))
        if suggestion is not SilenceSuggestion.NO_RESPONSE:
            state.nudges_this_question += 1
        else:
            # The ladder is exhausted for this question; stop accumulating until
            # the conversation layer arms the next question.
            state.running = False
        return suggestion

    def _state_for(self, session_id: str) -> _SessionSilenceState:
        state = self._sessions.get(session_id)
        if state is None:
            state = _SessionSilenceState()
            self._sessions[session_id] = state
        return state


def build_silence_coordinator(settings: Settings) -> SilenceCoordinator:
    """Phase 2 entry point for the LiveKit agent process: the ladder comes from
    env-overridable Settings fields, never from literals here."""
    return SilenceCoordinator(
        SilenceLadder(
            think_pause_seconds=settings.voice_think_pause_seconds,
            presence_check_seconds=settings.voice_presence_check_seconds,
            presence_retry_seconds=settings.voice_presence_retry_seconds,
            no_response_seconds=settings.voice_no_response_seconds,
            max_nudges_per_question=settings.voice_max_nudges_per_question,
        )
    )
