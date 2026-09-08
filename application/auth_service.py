"""HR user registration and authentication use cases.

No provider SDK here: password hashing lives in services/password_hashing.py,
persistence in services/db/hr_users.py. This module owns only the use-case
logic -- who's allowed to log in, and whether an email is already taken.
"""
from models.platform import HRUser
from services.db.hr_users import HRUserRepository
from services.password_hashing import hash_password, verify_password


class AuthServiceError(Exception):
    """Base class for authentication/authorization use-case failures."""


class InvalidCredentialsError(AuthServiceError):
    """Raised when login fails -- wrong email, wrong password, or inactive account.

    Deliberately the same error for all three cases so a caller can't use the
    response to enumerate which emails are registered.
    """


class EmailAlreadyRegisteredError(AuthServiceError):
    """Raised when registering an email that already has an account."""


class ForbiddenError(AuthServiceError):
    """Raised when an authenticated user attempts to access a resource they do
    not own. Distinct from a not-found: the resource exists, just not for them."""


class AuthService:
    def __init__(self, hr_user_repo: HRUserRepository) -> None:
        self._hr_user_repo = hr_user_repo

    def register(self, email: str, password: str, full_name: str) -> HRUser:
        if self._hr_user_repo.get_by_email(email) is not None:
            raise EmailAlreadyRegisteredError(f"'{email}' is already registered")
        user = HRUser(email=email, password_hash=hash_password(password), full_name=full_name)
        return self._hr_user_repo.create(user)

    def authenticate(self, email: str, password: str) -> HRUser:
        user = self._hr_user_repo.get_by_email(email)
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Invalid email or password")
        return user
