"""No-key default EmailPort. Demo / Mock Mode never needs email credentials."""
import hashlib

from services.email.base import EmailMessage, EmailPort, SendResult
from services.logging_service import Event, log_event


class NullEmailService(EmailPort):
    """Logs that a send happened -- never the recipient, subject, or body.

    Mirrors NullInterviewTransport and the mock LLM: Demo / Mock Mode (and any
    environment with EMAIL_PROVIDER unset) completes the entire pipeline,
    including the candidate-visible invitation email step, with no
    credentials and no external network call. Never presented as a real send:
    provider_message_id is always None.
    """

    provider_name = "null"

    def send(self, message: EmailMessage) -> SendResult:
        recipient_sha256 = hashlib.sha256(
            message.to_email.strip().casefold().encode("utf-8")
        ).hexdigest()
        log_event(Event.EMAIL_SENT, provider=self.provider_name, recipient_sha256=recipient_sha256)
        return SendResult(provider_message_id=None)
