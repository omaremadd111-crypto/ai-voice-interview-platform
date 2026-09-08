"""Per-turn latency instrumentation for the realtime voice path.

The goal of this phase is to prove the conversation feels natural. "Feels" is
the real acceptance test, but it is not debuggable on its own -- when a turn
lands badly you need to know whether the agent waited too long to decide the
user was done, or the model was slow to start, or the voice was slow to
speak. Those are three different fixes.

So every agent turn prints one line:

    turn  eou 520ms  llm_ttft 310ms  tts_ttfb 180ms  ->  e2e 1010ms

  eou       end-of-turn delay: from the user's last audio to the moment the
            turn was judged complete. This is the turn-taking tuning in
            turn_tuning.py showing its work. High here means the agent felt
            slow to *notice*, not slow to think.
  llm_ttft  LLM time to first token. Near zero means preemptive generation
            guessed the turn correctly and the model had already started.
  tts_ttfb  time to the first chunk of synthesised audio.
  e2e       the SDK's own end-to-end measurement: user stopped speaking ->
            agent started responding. This is what the user actually
            experienced, and it is not simply the sum of the three parts --
            preemptive generation overlaps them. Under ~800ms feels
            conversational; past ~1.5s people start talking over the agent.

The numbers come from ``ChatMessage.metrics``, which the SDK attaches to each
conversation item once the turn resolves. Nothing here changes behaviour, and
no transcript text is ever logged.
"""

from __future__ import annotations

import logging

from livekit.agents.voice import AgentSession
from livekit.agents.voice.events import ConversationItemAddedEvent

logger = logging.getLogger("interview_agent.livekit.v2.latency")


def _ms(report: dict, key: str) -> float | None:
    value = report.get(key)
    if value is None:
        return None
    try:
        return float(value) * 1000.0
    except (TypeError, ValueError):
        return None


def _fmt(value: float | None) -> str:
    return "  --  " if value is None else f"{value:.0f}ms"


def attach_latency_logging(session: AgentSession) -> None:
    """Log a one-line latency breakdown per agent turn.

    The end-of-turn delay is reported on the *user's* message and the
    generation timings on the *agent's* reply, so the user-side number is
    carried forward one turn to print the whole picture on a single line.
    """
    # Last observed end-of-turn delay, in ms. Belongs to the user turn that
    # the next agent reply is answering.
    pending_eou: dict[str, float | None] = {"value": None}

    def _on_item(event: ConversationItemAddedEvent) -> None:
        item = event.item
        report = getattr(item, "metrics", None) or {}
        role = getattr(item, "role", None)

        if role == "user":
            pending_eou["value"] = _ms(report, "end_of_turn_delay")
            return

        if role != "assistant":
            return

        eou = pending_eou["value"]
        pending_eou["value"] = None

        llm_ttft = _ms(report, "llm_node_ttft")
        tts_ttfb = _ms(report, "tts_node_ttfb")
        e2e = _ms(report, "e2e_latency")

        # The greeting, and any other agent speech not answering a user turn,
        # has no meaningful latency to report.
        if e2e is None and llm_ttft is None and tts_ttfb is None:
            return

        logger.info(
            "turn  eou %s  llm_ttft %s  tts_ttfb %s  ->  e2e %s",
            _fmt(eou),
            _fmt(llm_ttft),
            _fmt(tts_ttfb),
            _fmt(e2e),
        )

    session.on("conversation_item_added", _on_item)


def attach_turn_state_logging(session: AgentSession) -> None:
    """Log the session's own turn-taking state changes at DEBUG.

    Useful exactly once per tuning change: it shows whether the agent thought
    the user was still speaking during a pause that felt like it got cut off.
    These events carry state labels only -- never transcript content.
    """

    def _log(event: object) -> None:
        logger.debug(
            "state %s: %s -> %s",
            type(event).__name__,
            getattr(event, "old_state", None),
            getattr(event, "new_state", None),
        )

    session.on("user_state_changed", _log)
    session.on("agent_state_changed", _log)
