"""LiveKit Egress adapter: server-side mixed audio recording.

Room-composite egress with ``audio_only=True`` mixes every participant in the
room into one file -- the candidate and the AI interviewer together -- which is
what "a mixed recording containing candidate + AI" requires. Track egress would
give one file per participant and browser MediaRecorder would only ever capture
one side, so neither is used.

MP3 is used directly: ``EncodedFileType.MP3`` is natively supported by LiveKit
egress, so there is no transcoding step and nothing to fall back to.

**Where the file goes.** Egress runs on LiveKit's servers, not this machine, so
it cannot write to a local disk -- it uploads to object storage. Any
S3-compatible target works (AWS S3, MinIO, Cloudflare R2) because ``S3Upload``
carries ``endpoint`` and ``force_path_style``. Without configured storage this
gateway refuses to start a recording rather than starting one nobody can ever
retrieve.

Like ``gateway.py``, this is the only place ``livekit`` is imported for
recording, and no exception raised here carries credentials or provider bodies.
"""
import asyncio

import aiohttp
from livekit import api
from livekit.api.twirp_client import TwirpError

from config.settings import Settings
from services.livekit.base import (
    EgressStartRequest,
    EgressState,
    LiveKitConfigurationError,
    LiveKitEgressPort,
    LiveKitGatewayError,
)


def _state_from_info(info: object) -> EgressState:
    """Translate LiveKit's EgressInfo into the provider-neutral state.

    File results only exist once egress has produced a file, so size/duration
    stay None until then; that is the normal case for an ACTIVE recording, not
    an error.
    """
    file_results = list(getattr(info, "file_results", []) or [])
    first = file_results[0] if file_results else None

    duration_ns = getattr(first, "duration", None) if first is not None else None
    return EgressState(
        egress_id=getattr(info, "egress_id", "") or "",
        status=api.EgressStatus.Name(getattr(info, "status", 0)),
        started_at=(getattr(info, "started_at", 0) or 0) / 1e9 or None,
        ended_at=(getattr(info, "ended_at", 0) or 0) / 1e9 or None,
        # LiveKit reports duration in nanoseconds.
        duration_seconds=(duration_ns / 1e9) if duration_ns else None,
        size_bytes=(getattr(first, "size", None) or None) if first is not None else None,
        location=(getattr(first, "location", None) or None) if first is not None else None,
        error=getattr(info, "error", None) or None,
    )


class LiveKitEgressGateway(LiveKitEgressPort):
    def __init__(self, settings: Settings) -> None:
        self._url = settings.livekit_url
        self._api_key = settings.livekit_api_key
        self._api_secret = settings.livekit_api_secret
        self._bucket = settings.recording_s3_bucket
        self._region = settings.recording_s3_region
        self._endpoint = settings.recording_s3_endpoint
        self._access_key = settings.recording_s3_access_key
        self._secret = settings.recording_s3_secret_key
        self._force_path_style = settings.recording_s3_force_path_style

    # --- internals -----------------------------------------------------------

    def _require_credentials(self) -> tuple[str, str, str]:
        if not (self._url and self._api_key and self._api_secret):
            raise LiveKitConfigurationError(
                "LiveKit credentials are required to record an interview."
            )
        return self._url, self._api_key, self._api_secret

    def _file_output(self, filepath: str) -> api.EncodedFileOutput:
        if not (self._bucket and self._access_key and self._secret):
            raise LiveKitConfigurationError(
                "Recording storage is not configured. Set RECORDING_S3_BUCKET, "
                "RECORDING_S3_ACCESS_KEY and RECORDING_S3_SECRET_KEY. LiveKit egress "
                "uploads from its own servers and cannot write to this machine's disk."
            )
        upload = api.S3Upload(
            access_key=self._access_key,
            secret=self._secret,
            bucket=self._bucket,
            region=self._region or "",
            force_path_style=self._force_path_style,
        )
        if self._endpoint:
            # Set only when present: an empty endpoint makes the SDK target an
            # empty host instead of AWS.
            upload.endpoint = self._endpoint

        return api.EncodedFileOutput(
            file_type=api.EncodedFileType.MP3,
            filepath=filepath,
            s3=upload,
        )

    def _run(self, coro_factory, failure_message: str):
        """Run one egress call, converting provider errors into safe ones."""
        url, key, secret = self._require_credentials()

        async def _call():
            try:
                async with api.LiveKitAPI(
                    url=url, api_key=key, api_secret=secret
                ) as client:
                    return await coro_factory(client)
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                OSError,
                TwirpError,
            ) as exc:
                # Deliberately no provider body or credentials in the message.
                raise LiveKitGatewayError(
                    f"{failure_message} ({type(exc).__name__})"
                ) from exc

        return asyncio.run(_call())

    # --- port ----------------------------------------------------------------

    def start_room_audio_recording(self, request: EgressStartRequest) -> EgressState:
        file_output = self._file_output(request.filepath)

        async def _start(client):
            return await client.egress.start_room_composite_egress(
                api.RoomCompositeEgressRequest(
                    room_name=request.room_name,
                    # Mixes every participant -- candidate and AI -- into one file.
                    audio_only=True,
                    file_outputs=[file_output],
                )
            )

        return _state_from_info(
            self._run(_start, "LiveKit could not start the interview recording.")
        )

    def stop_recording(self, egress_id: str) -> EgressState:
        async def _stop(client):
            return await client.egress.stop_egress(
                api.StopEgressRequest(egress_id=egress_id)
            )

        return _state_from_info(
            self._run(_stop, "LiveKit could not stop the interview recording.")
        )

    def get_recording(self, egress_id: str) -> EgressState | None:
        async def _list(client):
            return await client.egress.list_egress(
                api.ListEgressRequest(egress_id=egress_id)
            )

        response = self._run(
            _list, "LiveKit could not report the interview recording status."
        )
        items = list(getattr(response, "items", []) or [])
        return _state_from_info(items[0]) if items else None
