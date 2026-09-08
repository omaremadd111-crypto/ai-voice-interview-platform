"""Official LiveKit server-SDK adapter.

No application module imports ``livekit``. Room dispatch and JWT generation are
confined here, behind ``LiveKitRoomPort``, with safe integration exceptions that
never include credentials or provider response bodies.
"""
import asyncio
from datetime import timedelta

import aiohttp
from livekit import api
from livekit.api.twirp_client import TwirpError

from config.settings import Settings
from services.livekit.base import (
    LiveKitConfigurationError,
    LiveKitDispatch,
    LiveKitGatewayError,
    LiveKitRoomPort,
    LiveKitToken,
)


class LiveKitRoomGateway(LiveKitRoomPort):
    def __init__(self, settings: Settings) -> None:
        self._url = settings.livekit_url
        self._api_key = settings.livekit_api_key
        self._api_secret = settings.livekit_api_secret

    def dispatch(self, dispatch: LiveKitDispatch) -> None:
        url, key, secret = self._require_credentials()

        async def create_dispatch() -> None:
            try:
                async with api.LiveKitAPI(url=url, api_key=key, api_secret=secret) as client:
                    await client.agent_dispatch.create_dispatch(
                        api.CreateAgentDispatchRequest(
                            agent_name=dispatch.agent_name,
                            room=dispatch.room_name,
                            metadata=dispatch.metadata,
                        )
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, TwirpError) as exc:
                raise LiveKitGatewayError(
                    "LiveKit could not create the voice room or dispatch the agent."
                ) from exc

        asyncio.run(create_dispatch())

    def create_participant_token(
        self,
        *,
        room_name: str,
        participant_identity: str,
        participant_name: str,
        ttl_seconds: int,
    ) -> LiveKitToken:
        url, key, secret = self._require_credentials()
        try:
            token = (
                api.AccessToken(key, secret)
                .with_identity(participant_identity)
                .with_name(participant_name)
                .with_ttl(timedelta(seconds=ttl_seconds))
                .with_grants(
                    api.VideoGrants(
                        room_join=True,
                        room=room_name,
                        can_publish=True,
                        can_subscribe=True,
                        can_publish_data=True,
                        can_publish_sources=["microphone"],
                    )
                )
                .to_jwt()
            )
        except (TypeError, ValueError) as exc:
            raise LiveKitGatewayError("LiveKit could not issue a room token.") from exc
        return LiveKitToken(
            token=token,
            server_url=url,
            room_name=room_name,
            participant_identity=participant_identity,
            participant_name=participant_name,
        )

    def close_room(self, room_name: str) -> None:
        url, key, secret = self._require_credentials()

        async def delete_room() -> None:
            try:
                async with api.LiveKitAPI(url=url, api_key=key, api_secret=secret) as client:
                    await client.room.delete_room(api.DeleteRoomRequest(room=room_name))
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, TwirpError) as exc:
                raise LiveKitGatewayError("LiveKit could not close the voice room.") from exc

        asyncio.run(delete_room())

    def _require_credentials(self) -> tuple[str, str, str]:
        if not self._url or not self._api_key or not self._api_secret:
            raise LiveKitConfigurationError(
                "LiveKit voice is not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY, "
                "and LIVEKIT_API_SECRET in .env."
            )
        return self._url, self._api_key, self._api_secret
