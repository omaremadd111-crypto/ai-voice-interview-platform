"""services/email/: EmailPort contract, the null default, the SMTP provider,
and the provider factory. No real network call anywhere in this file --
SmtpEmailService's smtplib.SMTP is mocked, exactly like other provider tests
in this suite mock their external client.
"""
import hashlib
import json
import logging
import smtplib
from unittest.mock import MagicMock, patch

import pytest

from config.settings import Settings
from services.email.base import EmailConfigurationError, EmailMessage, EmailProviderError
from services.email.factory import get_email_service
from services.email.null_service import NullEmailService
from services.email.smtp_service import SmtpEmailService


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


_MESSAGE = EmailMessage(
    to_email="candidate@example.com",
    subject="Your interview link",
    text_body="Hi Jordan, here is your interview link: https://example.com/interview/tok",
    html_body="<p>Hi Jordan</p>",
)


# --- NullEmailService --------------------------------------------------------

def test_null_email_service_never_logs_recipient_subject_or_body(captured_logs) -> None:
    result = NullEmailService().send(_MESSAGE)

    assert result.provider_message_id is None
    assert len(captured_logs.messages) == 1
    payload = json.loads(captured_logs.messages[0])
    assert payload["event"] == "EMAIL_SENT"
    assert payload["provider"] == "null"
    assert payload["recipient_sha256"] == hashlib.sha256(b"candidate@example.com").hexdigest()
    serialized = captured_logs.messages[0]
    assert "candidate@example.com" not in serialized
    assert "Your interview link" not in serialized
    assert "here is your interview link" not in serialized


def test_null_email_service_is_the_mock_mode_default_even_with_smtp_configured() -> None:
    """Mock Mode never sends real email, exactly like it never makes a real
    LLM call -- regardless of what EMAIL_PROVIDER is set to, even a fully
    valid one."""
    settings = Settings(
        mock_mode=True, email_provider="smtp",
        smtp_host="smtp.example.com", smtp_username="u", smtp_password="p",
        email_from_address="noreply@example.com",
    )
    service = get_email_service(settings)
    assert isinstance(service, NullEmailService)


def test_null_email_service_is_the_unconfigured_default() -> None:
    service = get_email_service(Settings(mock_mode=False, email_provider="null"))
    assert isinstance(service, NullEmailService)


# --- factory selection -------------------------------------------------------

def test_factory_selects_smtp_when_configured_and_not_mock_mode() -> None:
    settings = Settings(
        mock_mode=False, email_provider="smtp",
        smtp_host="smtp.example.com", smtp_username="u", smtp_password="p",
        email_from_address="noreply@example.com",
    )
    service = get_email_service(settings)
    assert isinstance(service, SmtpEmailService)


# --- SmtpEmailService --------------------------------------------------------

def _smtp_settings(**overrides: object) -> Settings:
    defaults = dict(
        mock_mode=False, email_provider="smtp",
        smtp_host="smtp.example.com", smtp_port=587,
        smtp_username="user", smtp_password="secret-password",
        smtp_use_tls=True,
        email_from_address="noreply@example.com", email_from_name="Acme Hiring",
        email_reply_to="hr@example.com",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_smtp_service_requires_host_and_from_address() -> None:
    with pytest.raises(EmailConfigurationError):
        SmtpEmailService(Settings(mock_mode=False, email_provider="null"))


def test_smtp_service_sends_via_smtplib_with_tls_and_login(captured_logs) -> None:
    settings = _smtp_settings()
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)

    with patch("services.email.smtp_service.smtplib.SMTP", return_value=fake_client) as smtp_cls:
        result = SmtpEmailService(settings).send(_MESSAGE)

    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=10.0)
    fake_client.starttls.assert_called_once()
    fake_client.login.assert_called_once_with("user", "secret-password")
    assert fake_client.send_message.call_count == 1
    sent_message = fake_client.send_message.call_args[0][0]
    assert sent_message["To"] == "candidate@example.com"
    assert sent_message["From"] == "Acme Hiring <noreply@example.com>"
    assert sent_message["Reply-To"] == "hr@example.com"
    assert sent_message["Subject"] == "Your interview link"
    assert result.provider_message_id is not None

    # Never logs the password used to authenticate, nor the recipient/body.
    serialized = " ".join(captured_logs.messages)
    assert "secret-password" not in serialized
    assert "candidate@example.com" not in serialized
    assert "here is your interview link" not in serialized


def test_smtp_service_skips_starttls_when_disabled() -> None:
    settings = _smtp_settings(smtp_use_tls=False)
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)

    with patch("services.email.smtp_service.smtplib.SMTP", return_value=fake_client):
        SmtpEmailService(settings).send(_MESSAGE)

    fake_client.starttls.assert_not_called()


def test_smtp_service_wraps_connection_failures_as_email_provider_error() -> None:
    settings = _smtp_settings()
    with patch("services.email.smtp_service.smtplib.SMTP", side_effect=OSError("connection refused")):
        with pytest.raises(EmailProviderError):
            SmtpEmailService(settings).send(_MESSAGE)


def test_smtp_service_wraps_auth_failures_as_email_provider_error() -> None:
    settings = _smtp_settings()
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Authentication failed")

    with patch("services.email.smtp_service.smtplib.SMTP", return_value=fake_client):
        with pytest.raises(EmailProviderError) as excinfo:
            SmtpEmailService(settings).send(_MESSAGE)

    # The wrapped error names the exception type only -- never the server's
    # raw response, which could itself echo back attempted-credential detail.
    assert "secret-password" not in str(excinfo.value)
