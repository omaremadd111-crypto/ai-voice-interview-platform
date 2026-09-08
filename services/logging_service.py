"""Structured JSON event logging. Never logs document text, answers, or secrets."""
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class Event(StrEnum):
    SESSION_CREATED = "SESSION_CREATED"
    DOCUMENT_PARSED = "DOCUMENT_PARSED"
    JOB_ANALYZED = "JOB_ANALYZED"
    CANDIDATE_ANALYZED = "CANDIDATE_ANALYZED"
    INTERVIEW_PLAN_CREATED = "INTERVIEW_PLAN_CREATED"
    INTERVIEW_STARTED = "INTERVIEW_STARTED"
    QUESTION_ANSWERED = "QUESTION_ANSWERED"
    FOLLOW_UP_CREATED = "FOLLOW_UP_CREATED"
    INTERVIEW_COMPLETED = "INTERVIEW_COMPLETED"
    EVIDENCE_DOWNGRADED = "EVIDENCE_DOWNGRADED"
    EVALUATION_COMPLETED = "EVALUATION_COMPLETED"
    REPORT_CREATED = "REPORT_CREATED"
    VOICE_INTENT_CLASSIFIED = "VOICE_INTENT_CLASSIFIED"
    VOICE_INTENT_FALLBACK = "VOICE_INTENT_FALLBACK"
    VOICE_STATE_TRANSITION = "VOICE_STATE_TRANSITION"
    VOICE_NO_RESPONSE = "VOICE_NO_RESPONSE"
    VOICE_REACTION_FILTERED = "VOICE_REACTION_FILTERED"
    VOICE_QUESTION_UNDELIVERED = "VOICE_QUESTION_UNDELIVERED"
    VOICE_TURN_LATENCY = "VOICE_TURN_LATENCY"
    API_REQUEST_TIMING = "API_REQUEST_TIMING"
    EMAIL_QUEUED = "EMAIL_QUEUED"
    EMAIL_SENT = "EMAIL_SENT"
    EMAIL_FAILED = "EMAIL_FAILED"


_LOGGER_NAME = "interview_agent"
_logger = logging.getLogger(_LOGGER_NAME)

_SENSITIVE_TEXT_KEYS = {
    "text", "answer", "document_text", "raw_text", "content", "cv_text",
    "description_text", "system", "user",
    "email", "to_email", "recipient", "body", "text_body", "html_body", "subject",
}
_SECRET_KEY_MARKERS = ("api_key", "secret", "token", "password", "credential")


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return any(marker in normalized for marker in _SECRET_KEY_MARKERS)


def content_metadata(label: str, content: str | bytes) -> dict[str, int | str]:
    """Return safe length and SHA-256 metadata without retaining raw content.

    Secret-like labels are rejected because even a secret hash should not be logged.
    String lengths are character counts; byte lengths are byte counts.
    """
    if not label or not label.isidentifier():
        raise ValueError("content metadata label must be a non-blank identifier")
    if _is_secret_key(label):
        raise ValueError("secret values must not be fingerprinted or logged")

    if isinstance(content, str):
        encoded = content.encode("utf-8")
        length = len(content)
    else:
        encoded = content
        length = len(content)
    return {
        f"{label}_length": length,
        f"{label}_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _sanitize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    safe_fields: dict[str, Any] = {}
    for key, value in fields.items():
        if _is_secret_key(key):
            continue
        if key in _SENSITIVE_TEXT_KEYS:
            if isinstance(value, (str, bytes)):
                safe_fields.update(content_metadata(key, value))
            continue
        safe_fields[key] = value
    return safe_fields


def configure_logging(level: str = "INFO") -> None:
    _logger.setLevel(level.upper())
    if not _logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(handler)
    _logger.propagate = False


def log_event(event: Event, session_id: str | None = None, **fields: Any) -> None:
    safe_fields = _sanitize_fields(fields)
    record = {
        "event": event.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        **safe_fields,
    }
    _logger.info(json.dumps(record, default=str))
