"""Provider-agnostic email send contract. No provider SDK imports here."""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel, Field


class EmailError(Exception):
    """Base class for all email-layer errors."""


class EmailConfigurationError(EmailError):
    """Raised when an explicitly selected provider is not usable as configured."""


class EmailProviderError(EmailError):
    """Raised when a real provider request fails. Callers treat this as a
    retryable send failure -- see application/email_dispatch_service.py."""


class EmailMessage(BaseModel):
    to_email: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    text_body: str = Field(min_length=1)
    #: Optional multipart/alternative rendering. Every template ships both.
    html_body: str | None = None


@dataclass(frozen=True)
class SendResult:
    #: Provider-assigned id for tracing a specific send, when the provider
    #: gives one. Stored on the outbox row; never required for correctness.
    provider_message_id: str | None = None


class EmailPort(ABC):
    #: "null" | "smtp" | ... -- for outbox/log tracing only, never branched on
    #: outside this package.
    provider_name: str

    @abstractmethod
    def send(self, message: EmailMessage) -> SendResult: ...
