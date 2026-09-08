"""Transport-neutral interview session persistence contracts.

The in-memory implementation is intentionally small, thread-safe, and copy-on-read/
copy-on-write. Callers can mutate a retrieved session freely, but those mutations do
not become persistent until ``save`` is called explicitly.
"""
from abc import ABC, abstractmethod
from threading import RLock

from models.session import InterviewSession


class SessionStoreError(Exception):
    """Base class for session persistence failures."""


class SessionNotFoundError(SessionStoreError):
    """Raised when a requested session id does not exist."""


class SessionAlreadyExistsError(SessionStoreError):
    """Raised when creating a session whose id is already stored."""


class StaleSessionVersionError(SessionStoreError):
    """Raised when save() targets a session whose stored version has moved on since
    the caller's copy was read. Every SessionStore implementation rejects a stale
    write rather than silently overwriting newer state; see InterviewSession.version.
    """


class SessionStore(ABC):
    @abstractmethod
    def create(self, session: InterviewSession) -> None: ...

    @abstractmethod
    def get(self, session_id: str) -> InterviewSession: ...

    @abstractmethod
    def save(self, session: InterviewSession) -> None: ...


class InMemorySessionStore(SessionStore):
    """Thread-safe process-local store with structural session isolation."""

    def __init__(self) -> None:
        self._sessions: dict[str, InterviewSession] = {}
        self._lock = RLock()

    def create(self, session: InterviewSession) -> None:
        session_id = validate_session_id(session.id)
        with self._lock:
            if session_id in self._sessions:
                raise SessionAlreadyExistsError(f"Session '{session_id}' already exists")
            self._sessions[session_id] = _deep_copy(session)

    def get(self, session_id: str) -> InterviewSession:
        normalized_id = validate_session_id(session_id)
        with self._lock:
            try:
                session = self._sessions[normalized_id]
            except KeyError as exc:
                raise SessionNotFoundError(f"Session '{normalized_id}' was not found") from exc
            return _deep_copy(session)

    def save(self, session: InterviewSession) -> None:
        session_id = validate_session_id(session.id)
        with self._lock:
            try:
                current = self._sessions[session_id]
            except KeyError as exc:
                raise SessionNotFoundError(f"Session '{session_id}' was not found") from exc
            if session.version != current.version:
                raise StaleSessionVersionError(
                    f"Session '{session_id}' was modified since it was last read "
                    f"(expected version {session.version}, current version {current.version})"
                )
            session.version += 1
            self._sessions[session_id] = _deep_copy(session)


def validate_session_id(session_id: str) -> str:
    normalized = session_id.strip() if session_id else ""
    if not normalized:
        raise SessionNotFoundError("Session id must not be blank")
    return normalized


def _deep_copy(session: InterviewSession) -> InterviewSession:
    return session.model_copy(deep=True)
