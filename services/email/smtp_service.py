"""SMTP EmailPort implementation. Stdlib smtplib/email.message only -- zero
new dependencies, works with any provider's SMTP relay (Gmail, SES, Postmark,
Mailgun, a self-hosted relay, ...).
"""
import hashlib
import smtplib
from email.message import EmailMessage as MimeMessage
from email.utils import make_msgid

from config.settings import Settings
from services.email.base import (
    EmailConfigurationError,
    EmailMessage,
    EmailPort,
    EmailProviderError,
    SendResult,
)
from services.logging_service import Event, log_event


class SmtpEmailService(EmailPort):
    provider_name = "smtp"

    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host or not settings.email_from_address:
            # Settings._email_requires_smtp_config already validates this at
            # startup when EMAIL_PROVIDER=smtp; this is a second, narrower
            # guard for direct construction (tests, scripts) that bypasses
            # load_settings().
            raise EmailConfigurationError(
                "SMTP_HOST and EMAIL_FROM_ADDRESS are required to construct SmtpEmailService."
            )
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._username = settings.smtp_username
        self._password = settings.smtp_password
        self._use_tls = settings.smtp_use_tls
        self._from_address = settings.email_from_address
        self._from_name = settings.email_from_name
        self._reply_to = settings.email_reply_to
        self._timeout_seconds = 10.0

    def send(self, message: EmailMessage) -> SendResult:
        mime = MimeMessage()
        mime["Subject"] = message.subject
        mime["From"] = (
            f"{self._from_name} <{self._from_address}>" if self._from_name else self._from_address
        )
        mime["To"] = message.to_email
        if self._reply_to:
            mime["Reply-To"] = self._reply_to
        message_id = make_msgid()
        mime["Message-Id"] = message_id
        mime.set_content(message.text_body)
        if message.html_body:
            mime.add_alternative(message.html_body, subtype="html")

        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout_seconds) as client:
                if self._use_tls:
                    client.starttls()
                if self._username and self._password:
                    client.login(self._username, self._password)
                client.send_message(mime)
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailProviderError(
                f"SMTP send to {self._host}:{self._port} failed: {type(exc).__name__}"
            ) from exc

        recipient_sha256 = hashlib.sha256(
            message.to_email.strip().casefold().encode("utf-8")
        ).hexdigest()
        log_event(Event.EMAIL_SENT, provider=self.provider_name, recipient_sha256=recipient_sha256)
        return SendResult(provider_message_id=message_id)
