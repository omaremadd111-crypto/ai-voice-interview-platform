"""Candidate-requested interview termination on the V2 realtime path.

Regression cover for a real recorded failure: the candidate said they were out
of time, the model produced a plausible closing of its own, and eight seconds
later the idle watchdog said "Take your time." The conversational layer had
understood the request, but nothing had moved the interview into a terminal
state, so ``director.is_finished`` stayed False and the watchdog -- correctly,
by its own rules -- armed and fired.

The fix gives V2 the termination path it never had: an ``end_interview``
function tool, mirroring the existing ``next_question`` tool that already drives
the plan-exhaustion ending. No second classifier and no phrase matching: the
model already in the session decides, exactly as it already decides when to call
``next_question``.

Timings here are milliseconds against the real event loop rather than a fake
clock, for the same reason tests/test_idle_watchdog.py uses them: these tests
exist to pin down task scheduling and cancellation interleavings, and a fake
clock steps straight over them.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from livekit.agents.llm import StopResponse

from models.agent_persona import default_persona
from models.common import QuestionCategory
from models.interview import NextPrompt
from services.livekit.v2 import agent_server_v2 as v2
from services.livekit.v2.director import VoiceInterviewDirector
from services.livekit.v2.idle_watchdog import (
    FIRST_NUDGE,
    SECOND_NUDGE,
    CandidateIdleWatchdog,
)

# Short enough to keep the suite fast, long enough that an await between
# emissions lands inside the window.
FIRST = 0.05
SECOND = 0.05
SETTLE = 0.15


class FakeStateEvent:
    def __init__(self, new_state: str, old_state: str | None = None) -> None:
        self.new_state = new_state
        self.old_state = old_state


class FakePlayout:
    async def wait_for_playout(self) -> None:
        return None


class FakeVoiceSession:
    """AgentSession stand-in covering both protocols under test.

    The watchdog needs ``on()`` plus the two state properties it re-reads after
    sleeping; the agent needs ``say()`` to return an awaitable playout handle.
    """

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}
        self.said: list[str] = []
        self.agent_state = "initializing"
        self.user_state = "listening"

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def say(self, text: str, **kwargs) -> FakePlayout:
        self.said.append(text)
        return FakePlayout()

    def generate_reply(self, **kwargs) -> None:  # pragma: no cover - must not run
        raise AssertionError("generate_reply must never run after termination")

    def emit(self, event: str, payload) -> None:
        for handler in self.handlers.get(event, []):
            handler(payload)

    def agent_becomes(self, state: str) -> None:
        old, self.agent_state = self.agent_state, state
        self.emit("agent_state_changed", FakeStateEvent(state, old))

    def finish_speaking(self) -> None:
        """The real sequence: agent speaks, then returns to listening."""
        self.agent_becomes("speaking")
        self.agent_becomes("listening")


class FakeAgentService:
    """Stands in for InterviewAgentService, counting terminal transitions."""

    def __init__(self, total: int = 3) -> None:
        self.total = total
        self.index = 0
        self.answers: list[str] = []
        self.end_calls = 0
        self.threads: dict[str, int] = {}

    def _prompt(self, finished: bool = False) -> NextPrompt:
        return NextPrompt(
            state="COMPLETED" if finished else "IN_PROGRESS",
            question_id=f"q{self.index + 1}",
            question_text=f"Question {self.index + 1} text",
            category=QuestionCategory.TECHNICAL,
            question_index=self.index,
            total_questions=self.total,
            progress_pct=0.0,
            finished=finished,
        )

    def start_interview(self, session_id: str) -> NextPrompt:
        self.index = 0
        return self._prompt()

    def submit_answer(self, session_id: str, answer: str, **kwargs) -> NextPrompt:
        self.answers.append(answer)
        self.index += 1
        return self._prompt(finished=self.index >= self.total)

    def end_interview(self, session_id: str):
        self.threads["end_interview"] = threading.get_ident()
        self.end_calls += 1
        return None


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text_content = text


class FakeTurnContext:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def add_message(self, role: str, content: str) -> None:
        self.messages.append((role, content))


class HarnessAgent(v2.InterviewVoiceAgentV2):
    """The real agent, with the session property pointed at a fake.

    ``Agent.session`` normally resolves through a running activity, which needs
    a live room. Everything else about the agent -- both tools, the closing
    latch, the turn guard -- is the production code path.
    """

    def __init__(self, *args, voice_session: FakeVoiceSession, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._voice_session = voice_session

    @property
    def session(self) -> FakeVoiceSession:  # type: ignore[override]
        return self._voice_session


def build(total: int = 3, *, with_watchdog: bool = True):
    """A started interview, wired the way build_server() wires it."""
    service = FakeAgentService(total=total)
    director = VoiceInterviewDirector(
        service,
        session_id="s-term-1",
        persona=default_persona("Aimy", "FlairsTech"),
        candidate_name="Omar Sayed",
        position_title="Junior AI Engineer",
        max_follow_ups_per_question=2,
    )
    session = FakeVoiceSession()
    watchdog = None
    if with_watchdog:
        watchdog = CandidateIdleWatchdog(
            session,
            first_nudge_seconds=FIRST,
            second_nudge_seconds=SECOND,
            is_finished=lambda: director.is_finished,
        )
        watchdog.attach()

    completed: list[int] = []

    async def on_completed() -> None:
        completed.append(1)

    agent = HarnessAgent(
        director,
        on_completed=on_completed,
        idle_watchdog=watchdog,
        voice_session=session,
    )
    return agent, director, service, session, watchdog, completed


def closing_text(director: VoiceInterviewDirector) -> str:
    return v2.render_script(
        director.persona.closing_script,
        persona=director.persona,
        candidate_name=director.candidate_name,
    )


# ===========================================================================
# 1. An explicit stop request ends the interview, once
# ===========================================================================


def test_stop_request_delivers_exactly_one_closing_and_completes() -> None:
    async def scenario():
        agent, director, service, session, _watchdog, completed = build()
        await director.start()

        await agent.end_interview(None)
        return session.said, service, director, completed

    said, service, director, completed = asyncio.run(scenario())

    assert said == [closing_text(director)]
    assert service.end_calls == 1
    assert completed == [1]
    assert director.is_finished is True
    assert director.is_terminating is True


def test_termination_uses_the_existing_terminal_transition_off_the_loop() -> None:
    """Same end_interview transition the text path already uses for a stop
    request, and still never run on the event loop."""

    async def scenario():
        agent, director, service, _s, _w, _c = build()
        await director.start()
        await agent.end_interview(None)
        return service.threads["end_interview"]

    assert asyncio.run(scenario()) != threading.get_ident()


def test_termination_records_no_answer_and_asks_nothing_further() -> None:
    """No submit_answer: it would move the cursor and can trigger the follow-up
    LLM -- generating a question during the ending is exactly what must not
    happen. The verbatim voice transcript still has the candidate's words."""

    async def scenario():
        agent, director, service, session, _w, _c = build()
        await director.start()
        director.note_candidate_speech("I really have to go now.")

        await agent.end_interview(None)
        return service.answers, session.said, director

    answers, said, director = asyncio.run(scenario())

    assert answers == []
    assert said == [closing_text(director)]


# ===========================================================================
# 2. Idempotency
# ===========================================================================


def test_duplicate_end_interview_calls_produce_one_closing_and_one_completion() -> None:
    async def scenario():
        agent, director, service, session, _w, completed = build()
        await director.start()

        for _ in range(4):
            await agent.end_interview(None)
        return session.said, service, completed, director

    said, service, completed, director = asyncio.run(scenario())

    assert said == [closing_text(director)]
    assert service.end_calls == 1
    assert completed == [1]


def test_concurrent_end_interview_calls_still_close_once() -> None:
    """The latch is claimed synchronously, so interleaved calls cannot both
    win it."""

    async def scenario():
        agent, director, service, session, _w, completed = build()
        await director.start()

        await asyncio.gather(*(agent.end_interview(None) for _ in range(5)))
        return session.said, service, completed, director

    said, service, completed, director = asyncio.run(scenario())

    assert said == [closing_text(director)]
    assert service.end_calls == 1
    assert completed == [1]


def test_terminate_is_idempotent_at_the_director() -> None:
    async def scenario():
        _a, director, service, _s, _w, _c = build()
        await director.start()

        await director.terminate()
        await director.terminate()
        await director.end_if_unfinished()
        return service.end_calls

    assert asyncio.run(scenario()) == 1


# ===========================================================================
# 3 + 4. The watchdog after termination -- both tiers
# ===========================================================================


def test_an_armed_watchdog_never_nudges_once_termination_begins() -> None:
    """The recorded bug, reproduced end to end: a timer is already pending when
    the candidate asks to stop."""

    async def scenario():
        agent, director, _svc, session, _w, _c = build()
        await director.start()

        # The agent finishes a turn: the watchdog arms, exactly as it did in the
        # real session.
        session.finish_speaking()
        await asyncio.sleep(FIRST / 5)

        await agent.end_interview(None)
        await asyncio.sleep(SETTLE * 2)
        return session.said, director

    said, director = asyncio.run(scenario())

    assert FIRST_NUDGE not in said
    assert said == [closing_text(director)]


def test_the_closing_itself_cannot_arm_a_new_nudge() -> None:
    """The closing is agent speech: its own speaking -> listening transition
    must not start the ladder over during shutdown."""

    async def scenario():
        agent, director, _svc, session, _w, _c = build()
        await director.start()

        await agent.end_interview(None)
        session.finish_speaking()  # the closing finishing playout
        await asyncio.sleep(SETTLE * 2)
        return session.said, director

    said, director = asyncio.run(scenario())
    assert said == [closing_text(director)]


def test_the_repeat_question_tier_never_fires_after_termination() -> None:
    """The second tier is the "Would you like me to repeat the question?"
    prompt. Repeated arming opportunities must produce neither tier."""

    async def scenario():
        agent, director, _svc, session, _w, _c = build()
        await director.start()

        session.finish_speaking()
        await asyncio.sleep(FIRST / 5)
        await agent.end_interview(None)

        # Five further chances to arm, spanning both tiers' delays.
        for _ in range(5):
            session.finish_speaking()
            await asyncio.sleep(SETTLE)
        return session.said, director

    said, director = asyncio.run(scenario())

    assert FIRST_NUDGE not in said
    assert SECOND_NUDGE not in said
    assert said == [closing_text(director)]


def test_a_timer_already_past_its_sleep_stays_silent_after_termination() -> None:
    """The interleaving cancellation alone cannot cover: the timer has already
    resumed past its await when termination lands. Driving _wait_then_nudge
    directly reproduces it deterministically."""

    async def scenario():
        agent, director, _svc, session, watchdog, _c = build()
        await director.start()
        session.agent_state = "listening"
        generation = watchdog._generation

        await agent.end_interview(None)
        # A timer scheduled before the stop request, waking up now.
        await watchdog._wait_then_nudge(0.0, generation)
        return session.said, director

    said, director = asyncio.run(scenario())
    assert said == [closing_text(director)]


def test_termination_silences_the_watchdog_before_it_awaits_anything() -> None:
    """Ordering guarantee: the watchdog is closed in the same synchronous step
    that claims the closing, before the engine round trip suspends."""

    async def scenario():
        agent, director, _svc, _session, watchdog, _c = build()
        await director.start()

        task = asyncio.create_task(agent.end_interview(None))
        # One yield: enough for the tool to run its synchronous prefix and
        # suspend inside the engine call, not enough for anything else.
        await asyncio.sleep(0)
        closed_early = watchdog._closed
        terminating_early = director.is_terminating
        await task
        return closed_early, terminating_early

    closed_early, terminating_early = asyncio.run(scenario())

    assert closed_early is True
    assert terminating_early is True


# ===========================================================================
# 5. next_question racing with termination
# ===========================================================================


def test_next_question_after_termination_emits_no_question() -> None:
    async def scenario():
        agent, director, service, session, _w, _c = build()
        await director.start()
        await agent.end_interview(None)

        result = await agent.next_question(None)
        return result, service, session, director

    result, service, session, director = asyncio.run(scenario())

    assert result == v2.CLOSING_DELIVERED
    assert "Question" not in result
    assert service.answers == []
    assert session.said == [closing_text(director)]


def test_next_question_racing_an_in_flight_termination_emits_no_question() -> None:
    """The real race: termination is suspended inside its engine call when the
    model's next_question tool call arrives."""

    async def scenario():
        agent, director, service, session, _w, _c = build()
        await director.start()

        task = asyncio.create_task(agent.end_interview(None))
        await asyncio.sleep(0)  # termination has claimed the closing and suspended
        result = await agent.next_question(None)
        await task
        return result, service, session, director

    result, service, session, director = asyncio.run(scenario())

    assert result == v2.CLOSING_DELIVERED
    assert service.answers == []
    assert service.end_calls == 1
    assert session.said == [closing_text(director)]


def test_advance_after_termination_is_inert_at_the_director() -> None:
    """Defence in depth: even called directly, the cursor cannot move."""

    async def scenario():
        _a, director, service, _s, _w, _c = build()
        await director.start()
        await director.terminate()

        view = await director.advance()
        return view, service

    view, service = asyncio.run(scenario())

    assert view.finished is True
    assert service.answers == []


def test_no_reply_is_generated_for_a_turn_after_termination() -> None:
    """StopResponse is the SDK's own "consume this turn silently" signal, so
    nothing can be spoken over the closing."""

    async def scenario():
        agent, director, _svc, _session, _w, _c = build()
        await director.start()
        await agent.end_interview(None)

        turn_ctx = FakeTurnContext()
        with pytest.raises(StopResponse):
            await agent.on_user_turn_completed(turn_ctx, FakeMessage("Are you there?"))
        return turn_ctx.messages

    assert asyncio.run(scenario()) == []


# ===========================================================================
# 6. Normal interviews are untouched
# ===========================================================================


def test_normal_silence_during_an_active_interview_still_nudges() -> None:
    """The watchdog must keep working exactly as before for a live interview."""

    async def scenario():
        _agent, director, _svc, session, _w, _c = build()
        await director.start()

        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == [FIRST_NUDGE]


def test_both_nudge_tiers_still_run_for_a_live_interview() -> None:
    async def scenario():
        _agent, director, _svc, session, _w, _c = build()
        await director.start()

        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        session.finish_speaking()
        await asyncio.sleep(SETTLE)
        return session.said

    assert asyncio.run(scenario()) == [FIRST_NUDGE, SECOND_NUDGE]


def test_a_normal_turn_is_briefed_as_before() -> None:
    async def scenario():
        agent, director, _svc, _session, _w, _c = build()
        await director.start()

        turn_ctx = FakeTurnContext()
        await agent.on_user_turn_completed(turn_ctx, FakeMessage("I used Terraform."))
        return turn_ctx.messages, director

    messages, director = asyncio.run(scenario())

    assert len(messages) == 1
    role, content = messages[0]
    assert role == "system"
    assert "CURRENT QUESTION" in content


# ===========================================================================
# 7. Plan exhaustion is unchanged
# ===========================================================================


def test_plan_exhaustion_still_closes_the_interview_exactly_as_before() -> None:
    async def scenario():
        agent, director, service, session, _w, completed = build(total=2)
        await director.start()

        first = await agent.next_question(None)
        director.note_candidate_speech("My second answer.")
        last = await agent.next_question(None)
        return first, last, session.said, service, completed, director

    first, last, said, service, completed, director = asyncio.run(scenario())

    assert "Question 2 text" in first
    assert last == v2.CLOSING_DELIVERED
    assert said == [closing_text(director)]
    assert completed == [1]
    assert director.is_finished is True
    # The normal path completes through the engine's own finished prompt, not
    # through the candidate-requested termination transition.
    assert director.is_terminating is False
    assert service.end_calls == 0


def test_a_stop_request_after_natural_completion_adds_no_second_closing() -> None:
    async def scenario():
        agent, director, service, session, _w, completed = build(total=1)
        await director.start()
        await agent.next_question(None)  # plan exhausted, closing delivered

        await agent.end_interview(None)
        return session.said, service, completed, director

    said, service, completed, director = asyncio.run(scenario())

    assert said == [closing_text(director)]
    assert completed == [1]
    assert service.end_calls == 0


def test_the_watchdog_is_still_silenced_by_plan_exhaustion() -> None:
    async def scenario():
        agent, director, _svc, session, _w, _c = build(total=1)
        await director.start()

        session.finish_speaking()
        await asyncio.sleep(FIRST / 5)
        await agent.next_question(None)
        await asyncio.sleep(SETTLE * 2)
        return session.said, director

    said, director = asyncio.run(scenario())
    assert said == [closing_text(director)]


# ===========================================================================
# Architecture: the fix must not reintroduce what V2 excluded
# ===========================================================================


def test_termination_adds_no_classifier_and_no_phrase_matching() -> None:
    """The model already in the session decides, exactly as it decides when to
    call next_question. No second LLM call, and no hardcoded phrases."""
    import ast
    import inspect

    source = inspect.getsource(v2)
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
            for alias in node.names:
                imported.add(alias.name)

    assert "VoiceIntentClassifier" not in imported
    assert "agents.voice_intent_classifier" not in imported

    tool = v2.InterviewVoiceAgentV2.end_interview.__wrapped__

    # It cannot match on wording it is never given: the tool receives no
    # utterance, transcript or message argument at all. The docstring DOES
    # describe the trigger in words -- that is the model-facing tool
    # description, which is the intended mechanism, not a phrase hack.
    assert list(inspect.signature(tool).parameters) == ["self", "context"]

    # And the executable body compares no strings: recognition is the model's
    # job, so no literal ever reaches a comparison here.
    import textwrap

    body = ast.parse(textwrap.dedent(inspect.getsource(tool))).body[0]
    assert isinstance(body, ast.AsyncFunctionDef)
    statements = body.body[1:] if ast.get_docstring(body) else body.body
    for node in statements:
        for inner in ast.walk(node):
            if isinstance(inner, ast.Compare):
                operands = [inner.left, *inner.comparators]
                assert not any(
                    isinstance(operand, ast.Constant) and isinstance(operand.value, str)
                    for operand in operands
                ), "end_interview must not compare against string literals"


def test_end_interview_is_exposed_to_the_model_as_a_tool() -> None:
    from livekit.agents.llm.tool_context import FunctionTool

    assert isinstance(v2.InterviewVoiceAgentV2.end_interview, FunctionTool)
    assert isinstance(v2.InterviewVoiceAgentV2.next_question, FunctionTool)


def test_the_model_is_instructed_to_call_the_tool_not_improvise_a_goodbye() -> None:
    instructions = v2.build_instructions(
        default_persona("Aimy", "FlairsTech"), "Omar Sayed", "Junior AI Engineer"
    )
    assert "end_interview tool" in instructions
    assert "Do not compose your own goodbye" in instructions
