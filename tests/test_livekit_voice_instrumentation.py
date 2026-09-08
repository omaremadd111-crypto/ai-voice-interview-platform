"""Phase 0 instrumentation: LiveKit user/agent state transitions are logged as
state labels and timestamps only -- never transcript content -- and change no
conversation behaviour. See services/livekit/agent_server.py.
"""
import json
import logging

import pytest

from services.livekit.agent_server import _instrument_voice_state_events


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


class FakeAgentSession:
    def __init__(self) -> None:
        self.handlers: dict[str, callable] = {}

    def on(self, event_name: str, callback) -> None:
        self.handlers[event_name] = callback


class FakeStateEvent:
    def __init__(self, event_type: str, old_state: str, new_state: str) -> None:
        self.type = event_type
        self.old_state = old_state
        self.new_state = new_state


def test_instrumentation_registers_both_state_event_handlers() -> None:
    session = FakeAgentSession()
    _instrument_voice_state_events(session, session_id="sess-1")
    assert "user_state_changed" in session.handlers
    assert "agent_state_changed" in session.handlers


def test_user_state_transition_is_logged_without_any_transcript_content(
    captured_logs: _ListHandler,
) -> None:
    session = FakeAgentSession()
    _instrument_voice_state_events(session, session_id="sess-1")

    session.handlers["user_state_changed"](
        FakeStateEvent("user_state_changed", "listening", "speaking")
    )

    assert len(captured_logs.messages) == 1
    payload = json.loads(captured_logs.messages[0])
    assert payload["event"] == "VOICE_STATE_TRANSITION"
    assert payload["session_id"] == "sess-1"
    assert payload["old_state"] == "listening"
    assert payload["new_state"] == "speaking"
    # The whole point of this instrumentation: it can never leak candidate
    # speech, because the event it observes never carries any.
    assert "answer" not in payload
    assert "text" not in payload


def test_agent_state_transition_is_logged(captured_logs: _ListHandler) -> None:
    session = FakeAgentSession()
    _instrument_voice_state_events(session, session_id="sess-2")

    session.handlers["agent_state_changed"](
        FakeStateEvent("agent_state_changed", "speaking", "listening")
    )

    payload = json.loads(captured_logs.messages[0])
    assert payload["event"] == "VOICE_STATE_TRANSITION"
    assert payload["session_id"] == "sess-2"
    assert payload["old_state"] == "speaking"
    assert payload["new_state"] == "listening"
