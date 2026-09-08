"""LiveKit integration boundary.

Every import from the LiveKit Python SDK stays in this package. Application and
API layers depend on the small protocols in ``base.py`` instead.
"""

from services.livekit.base import (
    LiveKitConfigurationError,
    LiveKitDispatch,
    LiveKitGatewayError,
    LiveKitRoomPort,
    LiveKitToken,
    room_name_for_session,
)
from services.livekit.gateway import LiveKitRoomGateway

__all__ = [
    "LiveKitConfigurationError",
    "LiveKitDispatch",
    "LiveKitGatewayError",
    "LiveKitRoomGateway",
    "LiveKitRoomPort",
    "LiveKitToken",
    "room_name_for_session",
]
