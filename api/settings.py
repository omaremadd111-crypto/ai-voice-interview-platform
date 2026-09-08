"""API-transport-only configuration: JWT signing and CORS.

Kept separate from config/settings.py's Settings, which stays scoped to core and
integration configuration -- JWT, invitation signing, and CORS are HTTP concerns
specific to this layer, not something Gradio or the worker needs.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Safe local-development default only -- never unrestricted in a real deployment.
_DEFAULT_DEV_ORIGINS = ("http://localhost:5173", "http://localhost:3000")


class APIConfigurationError(Exception):
    """Raised when required API configuration (e.g. JWT_SECRET_KEY) is missing."""


class APISettings(BaseModel):
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    cors_allowed_origins: list[str] = list(_DEFAULT_DEV_ORIGINS)
    # Candidate invitation signing is separate from LiveKit participant tokens.
    # It may intentionally reuse JWT_SECRET_KEY when no dedicated secret is set,
    # but is still loaded only from .env.
    voice_invite_secret: str | None = None
    # Kill switch for api/routers/public.py (the automated screening pipeline's
    # unauthenticated surface). False by default: an existing deployment gets no
    # new attack surface until this is explicitly turned on, and turning it back
    # off removes the whole public surface without a code change. See
    # api/app.py's create_app().
    public_applications_enabled: bool = False

    @field_validator("jwt_secret_key")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("jwt_secret_key must not be blank")
        return v

    @field_validator("jwt_access_token_expire_minutes")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("jwt_access_token_expire_minutes must be a positive integer")
        return v


def load_api_settings(env_file: str | Path | None = None) -> APISettings:
    dotenv_path = Path(env_file) if env_file is not None else PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)

    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise APIConfigurationError(
            "JWT_SECRET_KEY is not configured; set it in .env before running the API."
        )
    origins_raw = os.getenv("CORS_ALLOWED_ORIGINS")
    origins = (
        [origin.strip() for origin in origins_raw.split(",") if origin.strip()]
        if origins_raw
        else list(_DEFAULT_DEV_ORIGINS)
    )
    public_applications_raw = os.getenv("PUBLIC_APPLICATIONS_ENABLED")
    return APISettings(
        jwt_secret_key=secret,
        jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
        jwt_access_token_expire_minutes=int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60")),
        cors_allowed_origins=origins,
        voice_invite_secret=os.getenv("VOICE_INVITE_SECRET") or secret,
        public_applications_enabled=(
            public_applications_raw is not None
            and public_applications_raw.strip().lower() in {"1", "true", "yes", "on"}
        ),
    )
