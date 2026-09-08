"""Selects and constructs the configured EmailPort. No provider SDK imports here."""
from config.settings import Settings
from services.email.base import EmailPort
from services.email.null_service import NullEmailService
from services.email.smtp_service import SmtpEmailService


def get_email_service(settings: Settings) -> EmailPort:
    # Mock Mode never sends real email, exactly like it never makes a real LLM
    # call -- regardless of what EMAIL_PROVIDER happens to be set to.
    if settings.mock_mode:
        return NullEmailService()

    if settings.email_provider == "null":
        return NullEmailService()

    if settings.email_provider == "smtp":
        return SmtpEmailService(settings)

    raise ValueError(f"Unsupported email_provider '{settings.email_provider}'")
