"""Structured logging: required fields, redaction of sensitive keys, event vocabulary."""
import hashlib
import json
import logging

import pytest

from services.logging_service import Event, content_metadata, log_event


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


def test_log_event_emits_json_with_required_fields(captured_logs: _ListHandler) -> None:
    log_event(Event.SESSION_CREATED, session_id="sess-1", company="Acme")
    assert len(captured_logs.messages) == 1
    payload = json.loads(captured_logs.messages[0])
    assert payload["event"] == "SESSION_CREATED"
    assert payload["session_id"] == "sess-1"
    assert payload["company"] == "Acme"
    assert "timestamp" in payload


def test_log_event_redacts_sensitive_fields(captured_logs: _ListHandler) -> None:
    log_event(
        Event.CANDIDATE_ANALYZED,
        session_id="sess-2",
        api_key="sk-should-not-appear",
        answer="raw candidate answer text",
        document_text="raw jd text",
        cv_text="raw cv text",
        skill_count=5,
    )
    payload = json.loads(captured_logs.messages[-1])
    assert "api_key" not in payload
    assert "answer" not in payload
    assert "document_text" not in payload
    assert "cv_text" not in payload
    assert payload["answer_length"] == len("raw candidate answer text")
    assert payload["answer_sha256"] == hashlib.sha256(
        b"raw candidate answer text"
    ).hexdigest()
    assert payload["document_text_length"] == len("raw jd text")
    assert payload["cv_text_length"] == len("raw cv text")
    assert payload["skill_count"] == 5
    serialized = captured_logs.messages[-1]
    assert "sk-should-not-appear" not in serialized
    assert "raw candidate answer text" not in serialized
    assert "raw jd text" not in serialized
    assert "raw cv text" not in serialized


def test_log_event_redacts_email_fields(captured_logs: _ListHandler) -> None:
    """Phase 3 (auto screening pipeline): a caller passing a raw recipient
    address, subject, or body into log_event by mistake must still never see
    it reach the log line -- the same guarantee content_metadata already
    gives document/CV text."""
    log_event(
        Event.EMAIL_SENT,
        outbox_id=7,
        email="candidate@example.com",
        to_email="candidate@example.com",
        recipient="candidate@example.com",
        subject="Your interview link",
        body="Hi Jordan, here is your link...",
        text_body="Hi Jordan, here is your link...",
        html_body="<p>Hi Jordan</p>",
    )
    payload = json.loads(captured_logs.messages[-1])
    for raw_key in ("email", "to_email", "recipient", "subject", "body", "text_body", "html_body"):
        assert raw_key not in payload
    assert payload["email_sha256"] == hashlib.sha256(b"candidate@example.com").hexdigest()
    assert payload["outbox_id"] == 7
    serialized = captured_logs.messages[-1]
    assert "candidate@example.com" not in serialized
    assert "Your interview link" not in serialized
    assert "here is your link" not in serialized


def test_log_event_redacts_openrouter_api_key(captured_logs: _ListHandler) -> None:
    """The same generic _is_secret_key substring match ("api_key") that
    already redacts every other provider's key also covers the OpenRouter
    provider without any provider-specific redaction code. (The OpenRouter
    provider itself, services/llm/openrouter_service.py, never calls
    log_event at all -- this only guards against the field being logged
    from elsewhere in the future.)"""
    log_event(
        Event.SESSION_CREATED,
        session_id="sess-3",
        openrouter_api_key="sk-or-should-not-appear",
    )
    payload = json.loads(captured_logs.messages[-1])
    assert "openrouter_api_key" not in payload
    assert "sk-or-should-not-appear" not in json.dumps(payload)


def test_content_metadata_returns_only_length_and_sha256() -> None:
    metadata = content_metadata("answer", "candidate response")
    assert metadata == {
        "answer_length": len("candidate response"),
        "answer_sha256": hashlib.sha256(b"candidate response").hexdigest(),
    }
    assert "candidate response" not in metadata.values()


def test_content_metadata_rejects_secret_labels() -> None:
    with pytest.raises(ValueError):
        content_metadata("api_key", "sk-sensitive")


def test_log_event_without_session_id() -> None:
    # Should not raise even before a session exists (e.g. startup events).
    log_event(Event.SESSION_CREATED)


def test_all_spec_events_are_defined() -> None:
    # The 11 events named explicitly in SPEC 20 ("log events such as" -- illustrative,
    # not a closed list), plus documented additive events: EVIDENCE_DOWNGRADED
    # (Phase 6, evaluator downgrades an LLM-claimed evidence item that does not trace
    # back to the transcript), the three voice conversational-quality events (Phase 1
    # of the voice semantic-intent plan) -- VOICE_INTENT_CLASSIFIED/VOICE_INTENT_FALLBACK
    # log the classifier's label/confidence/latency only, and VOICE_STATE_TRANSITION
    # logs LiveKit's own turn-taking state labels; none of the three ever carries
    # transcript content -- VOICE_NO_RESPONSE (Phase 2 silence layer), which logs
    # question id, nudge count, elapsed seconds, buffer flag, and buffered length/hash
    # only: never candidate words -- and VOICE_REACTION_FILTERED (Phase 3 safety
    # gate), which logs the filtered generated text's length and hash only --
    # and VOICE_QUESTION_UNDELIVERED (P0-2 barge-in fix), which logs only
    # session_id and question_id when a forced interruption (not ordinary
    # candidate audio, which allow_interruptions=False already blocks) still
    # cuts off a question the engine already committed to -- and
    # VOICE_TURN_LATENCY (P0-4), which logs only session_id, question_id, and
    # numeric millisecond timings for each pipeline stage of one candidate
    # turn: never transcript content -- and API_REQUEST_TIMING (P0 perf audit),
    # emitted once per HTTP request by api/request_timing.py, which logs only
    # method, path, status_code, and numeric millisecond/count timings (total,
    # SQL, non-SQL, statement count): never request/response bodies, headers,
    # or query parameter values -- and EMAIL_QUEUED/EMAIL_SENT/EMAIL_FAILED
    # (auto screening pipeline Phase 3), which log template name, outbox id,
    # attempt count, and provider name only: never a recipient address,
    # subject, or body (services/email/, application/email_dispatch_service.py).
    expected = {
        "SESSION_CREATED", "DOCUMENT_PARSED", "JOB_ANALYZED", "CANDIDATE_ANALYZED",
        "INTERVIEW_PLAN_CREATED", "INTERVIEW_STARTED", "QUESTION_ANSWERED",
        "FOLLOW_UP_CREATED", "INTERVIEW_COMPLETED", "EVIDENCE_DOWNGRADED",
        "EVALUATION_COMPLETED", "REPORT_CREATED",
        "VOICE_INTENT_CLASSIFIED", "VOICE_INTENT_FALLBACK", "VOICE_STATE_TRANSITION",
        "VOICE_NO_RESPONSE", "VOICE_REACTION_FILTERED", "VOICE_QUESTION_UNDELIVERED",
        "VOICE_TURN_LATENCY", "API_REQUEST_TIMING",
        "EMAIL_QUEUED", "EMAIL_SENT", "EMAIL_FAILED",
    }
    assert {e.value for e in Event} == expected
