import base64
import json

from config.settings import Settings
from services.livekit.gateway import LiveKitRoomGateway


def _decode_segment(segment: str) -> dict:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


def test_candidate_jwt_is_room_scoped_and_microphone_only() -> None:
    gateway = LiveKitRoomGateway(
        Settings(
            livekit_url="wss://test.livekit.cloud",
            livekit_api_key="test-key",
            livekit_api_secret="test-secret-that-is-long-enough-for-signing",
        )
    )

    issued = gateway.create_participant_token(
        room_name="hr-screen-session-1",
        participant_identity="candidate-2",
        participant_name="Sam Taylor",
        ttl_seconds=300,
    )
    payload = _decode_segment(issued.token.split(".")[1])

    assert issued.server_url == "wss://test.livekit.cloud"
    assert payload["sub"] == "candidate-2"
    assert payload["name"] == "Sam Taylor"
    assert payload["video"]["room"] == "hr-screen-session-1"
    assert payload["video"]["roomJoin"] is True
    assert payload["video"]["canPublishSources"] == ["microphone"]
