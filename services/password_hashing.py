"""Password hashing: stdlib PBKDF2-HMAC-SHA256, no third-party crypto dependency.

Iteration count follows OWASP's 2023 Password Storage Cheat Sheet recommendation
for PBKDF2-HMAC-SHA256. Never logs or stores a plaintext password -- only this
module's hash() output (itself already irreversible) is ever persisted.
"""
import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16


def hash_password(plain_password: str) -> str:
    if not plain_password:
        raise ValueError("password must not be blank")
    salt = secrets.token_hex(_SALT_BYTES)
    digest = _derive(plain_password, salt, _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_str, salt, expected_digest = stored_hash.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    try:
        iterations = int(iterations_str)
    except ValueError:
        return False
    actual_digest = _derive(plain_password, salt, iterations)
    return hmac.compare_digest(actual_digest, expected_digest)


def _derive(plain_password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), iterations,
    ).hex()
