"""Proactive nudges when a candidate goes silent after a question.

The problem this solves is NOT endpointing. Endpointing decides when a
candidate has *finished* speaking; this decides what to do when they never
*start*. With the stable V2 turn-taking config, a candidate who says nothing at
all leaves the session sitting in agent="listening" / user="listening"
indefinitely, because there is no utterance to endpoint.

Deliberately outside the realtime turn-detection path: it reads session state
events and speaks fixed strings. It never touches STT, the turn detector,
endpointing, interruption handling or preemptive generation, and it makes no
LLM call -- the nudges are constants, so they add no generation latency.

This is NOT a revival of the old SilenceCoordinator. That component sat in the
conversation path, tracked per-question ladders across four escalation tiers
with presence checks and nudge budgets threaded through the interview core, and
drove replies through the conversation service. This one owns two timers and a
counter, subscribes to two SDK events, and is entirely self-contained.

Arming rule (the part that matters for correctness): the watchdog arms ONLY on
the agent's speaking -> listening transition -- never on the user's. A candidate
who pauses mid-answer puts user_state back to "listening" without the agent
having spoken, so no arm occurs and no nudge can fire inside their answer. That
is a structural guarantee, not a timing coincidence.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from livekit.agents.voice import AgentSession

logger = logging.getLogger("interview_agent.livekit.v2.idle")

#: Spoken verbatim. Deterministic by requirement: no LLM call, no generation
#: latency, and identical wording every time so it can be judged and tested.
FIRST_NUDGE = "Take your time."
SECOND_NUDGE = "Would you like me to repeat the question?"

#: Hard cap per unanswered question. Two nudges, then silence -- the watchdog
#: must never nag.
MAX_NUDGES_PER_QUESTION = 2


class CandidateIdleWatchdog:
    """Speaks at most two fixed nudges when a candidate stays silent.

    One instance per voice session. Not thread-safe by design: every method
    runs on the session's own event loop, the same loop the SDK emits its
    events on.
    """

    def __init__(
        self,
        session: AgentSession,
        *,
        first_nudge_seconds: float,
        second_nudge_seconds: float,
        is_finished: Callable[[], bool] | None = None,
    ) -> None:
        self._session = session
        self._first_delay = first_nudge_seconds
        self._second_delay = second_nudge_seconds
        # Lets the watchdog stay quiet once the interview is over, without it
        # needing to know anything about the interview engine.
        self._is_finished = is_finished or (lambda: False)

        # Monotonic token identifying the current question's watchdog run. Every
        # scheduled timer captures the value it was created under and re-checks
        # it before speaking, so a timer outliving its question is inert rather
        # than merely cancelled -- cancellation alone loses races against a
        # timer already past its await.
        self._generation = 0
        self._nudges_sent = 0
        self._timer: asyncio.Task | None = None
        self._closed = False

    # --- lifecycle -----------------------------------------------------------

    def attach(self) -> None:
        """Subscribe to the two session events that drive the watchdog."""
        self._session.on("agent_state_changed", self._on_agent_state)
        self._session.on("user_state_changed", self._on_user_state)
        # Shutdown must silence pending timers even if teardown never calls
        # aclose() -- e.g. the session closing on an error path.
        self._session.on("close", lambda _event: self.close())

    def close(self) -> None:
        """Permanently stop the watchdog. Idempotent."""
        self._closed = True
        self._cancel_timer()

    async def aclose(self) -> None:
        """Stop and await the in-flight timer, for deterministic teardown."""
        self._closed = True
        timer = self._timer
        self._cancel_timer()
        if timer is not None:
            # The timer only ever sleeps or calls say(); awaiting its
            # cancellation cannot block teardown meaningfully.
            try:
                await timer
            except (asyncio.CancelledError, Exception):
                pass

    def notify_question_changed(self) -> None:
        """Reset the nudge budget for a newly-asked question.

        Called by the interview agent when the engine advances. Bumping the
        generation here is what makes a timer scheduled under question A
        incapable of speaking during question B, even if it was already awake
        and past its cancellation point.
        """
        self._generation += 1
        self._nudges_sent = 0
        self._cancel_timer()

    # --- event handlers ------------------------------------------------------

    def _on_agent_state(self, event: object) -> None:
        new_state = getattr(event, "new_state", None)

        if new_state == "listening":
            # The agent has finished playing its audio and is now waiting on the
            # candidate. This is the only condition that arms the watchdog.
            self._arm()
            return

        # "speaking" (TTS playback), "thinking" (LLM generation AND tool
        # execution -- the SDK sets "thinking" when a tool produced output) and
        # "initializing" all mean the agent is busy, so any pending nudge is
        # no longer appropriate.
        self._cancel_timer()

    def _on_user_state(self, event: object) -> None:
        # VAD-driven and fires before STT produces a transcript, so this is the
        # earliest available signal that the candidate has begun speaking.
        if getattr(event, "new_state", None) != "speaking":
            # "listening" here is a pause, not an answer; re-arming on it would
            # let a nudge land inside the candidate's own answer. "away" is the
            # SDK's own idle marker and is likewise not a reason to act.
            return

        # A speaking candidate ends this question's idle episode outright: bump
        # the generation so an already-awake timer cannot speak over them, and
        # clear the budget so their answer is not followed by a stale nudge.
        self._generation += 1
        self._nudges_sent = 0
        self._cancel_timer()

    # --- internals -----------------------------------------------------------

    def _arm(self) -> None:
        if self._closed or self._is_finished():
            return
        if self._nudges_sent >= MAX_NUDGES_PER_QUESTION:
            # Budget spent for this question. Staying silent is the required
            # behaviour -- never nag.
            return
        # Only one timer may be pending per question; re-arming replaces it.
        self._cancel_timer()

        delay = self._first_delay if self._nudges_sent == 0 else self._second_delay
        generation = self._generation
        self._timer = asyncio.create_task(
            self._wait_then_nudge(delay, generation),
            name=f"idle-watchdog-gen{generation}",
        )

    def _cancel_timer(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _wait_then_nudge(self, delay: float, generation: int) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        # Re-validate everything after the sleep. Cancellation is not enough on
        # its own: a timer can be past its cancellation point when the world
        # changes, so the decision to speak is re-made here against current
        # state rather than trusted from when the timer was scheduled.
        if self._closed or generation != self._generation:
            return
        if self._is_finished():
            return
        if self._nudges_sent >= MAX_NUDGES_PER_QUESTION:
            return
        # The candidate may have started speaking, or the agent may have begun
        # a reply, in the instant between the sleep ending and this check.
        if self._session.user_state == "speaking":
            return
        if self._session.agent_state != "listening":
            return

        text = FIRST_NUDGE if self._nudges_sent == 0 else SECOND_NUDGE
        self._nudges_sent += 1
        logger.info(
            "idle nudge %d/%d after %.1fs silence",
            self._nudges_sent,
            MAX_NUDGES_PER_QUESTION,
            delay,
        )

        # Interruptible on purpose: a candidate who starts answering over the
        # nudge must be able to take the floor immediately.
        self._session.say(text)
        # The say() above drives agent_state speaking -> listening, and that
        # transition re-arms the watchdog for the next tier. No explicit
        # re-arm here, which keeps a single arming path.
