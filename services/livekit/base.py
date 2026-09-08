"""Provider-neutral port used by voice application services and transport."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


class LiveKitConfigurationError(Exception):
    """Raised when voice is selected without server-side credentials."""


class LiveKitGatewayError(Exception):
    """Raised when a room operation or token issue fails safely."""


def room_name_for_session(session_id: str) -> str:
    """Stable, non-PII room name used by worker, API, and voice agent."""
    return f"hr-screen-{session_id}"


@dataclass(frozen=True)
class LiveKitDispatch:
    room_name: str
    agent_name: str
    metadata: str


@dataclass(frozen=True)
class LiveKitToken:
    token: str
    server_url: str
    room_name: str
    participant_identity: str
    participant_name: str


class LiveKitRoomPort(ABC):
    @abstractmethod
    def dispatch(self, dispatch: LiveKitDispatch) -> None: ...

    @abstractmethod
    def create_participant_token(
        self,
        *,
        room_name: str,
        participant_identity: str,
        participant_name: str,
        ttl_seconds: int,
    ) -> LiveKitToken: ...

    @abstractmethod
    def close_room(self, room_name: str) -> None: ...


@dataclass(frozen=True)
class EgressStartRequest:
    """Start a mixed audio recording of one interview room."""

    room_name: str
    #: Object-storage key the recording is written to.
    filepath: str


@dataclass(frozen=True)
class EgressState:
    """What LiveKit reports about a recording, provider-neutral.

    ``status`` uses LiveKit's own vocabulary ("EGRESS_ACTIVE" etc.); mapping it
    onto the product's RecordingStatus happens in the recording coordinator so
    this port stays a thin translation of the provider.
    """

    egress_id: str
    status: str
    started_at: float | None = None
    ended_at: float | None = None
    duration_seconds: float | None = None
    size_bytes: int | None = None
    location: str | None = None
    error: str | None = None


class LiveKitEgressPort(ABC):
    """Recording operations, separate from LiveKitRoomPort on purpose.

    Adding these to LiveKitRoomPort would break every existing fake in the test
    suite and in the queue transport, none of which record anything. A separate
    port means recording can be absent, faked or disabled without touching the
    dispatch path that real interviews already depend on.
    """

    @abstractmethod
    def start_room_audio_recording(self, request: EgressStartRequest) -> EgressState: ...

    @abstractmethod
    def stop_recording(self, egress_id: str) -> EgressState: ...

    @abstractmethod
    def get_recording(self, egress_id: str) -> EgressState | None: ...
