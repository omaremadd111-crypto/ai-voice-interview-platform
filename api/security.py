"""JWT bearer-token issuance and verification. The only module that imports pyjwt.

Tokens carry only a user id ("sub") and expiry -- never a password, password
hash, or any candidate/session data.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from api.settings import APISettings


class TokenError(Exception):
    """Raised when a bearer token is missing, malformed, expired, or invalid."""


def create_access_token(user_id: int, api_settings: APISettings) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=api_settings.jwt_access_token_expire_minutes),
    }
    return jwt.encode(payload, api_settings.jwt_secret_key, algorithm=api_settings.jwt_algorithm)


def decode_access_token(token: str, api_settings: APISettings) -> int:
    try:
        payload = jwt.decode(token, api_settings.jwt_secret_key, algorithms=[api_settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid or expired token") from exc
    subject = payload.get("sub")
    if subject is None:
        raise TokenError("Token is missing its subject")
    try:
        return int(subject)
    except ValueError as exc:
        raise TokenError("Token subject is not a valid user id") from exc
