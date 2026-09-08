"""Signed candidate invitations and short-lived LiveKit participant tokens."""
import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from pydantic import BaseModel, Field, ValidationError

from application.queue_service import QueueService
from application.voice_conversation_service import resolve_voice_persona
from config.settings import Settings
from models.common import IN_FLIGHT_QUEUE_ITEM_STATUSES
from services.db.recordings import ConsentRepository
from models.platform import HRUser, InterviewConsentRecord
from services.db.candidates import CandidateRepository
from services.db.agent_configs import AgentConfigRepository
from services.db.positions import PositionRepository
from services.db.queues import QueueRepository
from services.livekit.base import LiveKitRoomPort, room_name_for_session


class VoiceInviteError(Exception):
    """Base class for safe invitation failures."""


class InvalidVoiceInviteError(VoiceInviteError):
    """Raised when an invitation was changed or does not match the queue item."""


class ExpiredVoiceInviteError(VoiceInviteError):
    """Raised when a signed candidate invitation is past its deadline."""


#: The exact wording a candidate agrees to. Stored verbatim with every consent
#: record, because "they consented" only means something alongside what they
#: were told. Changing this text requires bumping RECORDING_CONSENT_VERSION so
#: older consents stay attributable to the wording they were actually shown.
RECORDING_CONSENT_TEXT = (
    "This interview is conducted by an AI screening assistant and will be "
    "audio recorded. The recording and a written transcript are stored securely "
    "and reviewed by the hiring team at this company. You can ask questions at "
    "any time, and you may end the interview by closing this window. By "
    "continuing you confirm that you have read this and consent to being "
    "recorded."
)


class VoiceConsentRequiredError(Exception):
    """Raised when a recorded interview is joined without stored consent.

    Distinct from the other invite errors because it is not a failure -- the
    link is valid, the candidate simply has not agreed yet, and the frontend
    must show the notice rather than an error.
    """


class VoiceRoomNotReadyError(VoiceInviteError):
    """Raised when the worker has not prepared the session, or it already ended."""


class VoiceInviteClaims(BaseModel):
    queue_id: int
    item_id: int
    candidate_id: int
    session_id: str = Field(min_length=1)
    room_name: str = Field(min_length=1)
    exp: int


class VoiceInvitation(BaseModel):
    invite_url: str
    expires_at: datetime


class VoiceRoomToken(BaseModel):
    token: str
    server_url: str
    room_name: str
    participant_identity: str
    participant_name: str
    agent_name: str
    company_name: str


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VoiceInviteSigner:
    """Minimal HMAC envelope; the signed body contains ids only, never secrets."""

    def __init__(self, secret: str) -> None:
        if not secret.strip():
            raise ValueError("Voice invitation secret must not be blank")
        self._secret = secret.encode("utf-8")

    def sign(self, claims: VoiceInviteClaims) -> str:
        body = self._encode(claims.model_dump_json().encode("utf-8"))
        signature = self._encode(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def verify(self, token: str) -> VoiceInviteClaims:
        try:
            body, signature = token.split(".", 1)
            expected = self._encode(
                hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise InvalidVoiceInviteError("The voice invitation is invalid.")
            payload = json.loads(self._decode(body).decode("utf-8"))
            return VoiceInviteClaims.model_validate(payload)
        except InvalidVoiceInviteError:
            raise
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            raise InvalidVoiceInviteError("The voice invitation is invalid.") from exc

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class VoiceInviteService:
    def __init__(
        self,
        queue_service: QueueService,
        queue_repo: QueueRepository,
        candidate_repo: CandidateRepository,
        position_repo: PositionRepository,
        agent_config_repo: AgentConfigRepository,
        gateway: LiveKitRoomPort,
        signer: VoiceInviteSigner,
        settings: Settings,
        clock: Clock = utc_now,
        consent_repo: "ConsentRepository | None" = None,
    ) -> None:
        self._queue_service = queue_service
        self._queue_repo = queue_repo
        self._candidate_repo = candidate_repo
        self._position_repo = position_repo
        self._agent_config_repo = agent_config_repo
        self._gateway = gateway
        self._signer = signer
        self._settings = settings
        self._clock = clock
        # Optional so every existing construction (and every existing test)
        # keeps working unchanged. Required in practice only when recording is
        # enabled -- see _require_consent.
        self._consent_repo = consent_repo

    def create_invitation(self, owner: HRUser, queue_id: int, item_id: int) -> VoiceInvitation:
        self._queue_service.get(owner, queue_id)
        item = self._queue_repo.get_item(item_id)
        if item.queue_id != queue_id:
            raise InvalidVoiceInviteError("The queue item does not belong to this queue.")
        if (
            item.status not in IN_FLIGHT_QUEUE_ITEM_STATUSES
            or item.interview_session_id is None
        ):
            raise VoiceRoomNotReadyError(
                "The voice room is available after the worker prepares this interview."
            )
        now = self._clock()
        expires_at = now + timedelta(seconds=self._settings.voice_join_timeout_seconds)
        claims = VoiceInviteClaims(
            queue_id=queue_id,
            item_id=item_id,
            candidate_id=item.candidate_id,
            session_id=item.interview_session_id,
            room_name=room_name_for_session(item.interview_session_id),
            exp=int(expires_at.timestamp()),
        )
        signed = self._signer.sign(claims)
        return VoiceInvitation(
            invite_url=f"{self._settings.voice_public_base_url}/voice/{quote(signed)}",
            expires_at=expires_at,
        )

    def record_consent(self, invitation: str) -> InterviewConsentRecord:
        """Store the candidate's consent to being recorded.

        Verifies the invitation first, so consent can only be given by someone
        holding a valid link for that specific interview. Idempotent: a reload
        and re-accept keeps the original timestamp.
        """
        claims = self._verified_claims(invitation)
        if self._consent_repo is None:
            raise VoiceRoomNotReadyError("Consent storage is not configured.")
        return self._consent_repo.record(
            InterviewConsentRecord(
                interview_session_id=claims.session_id,
                candidate_id=claims.candidate_id,
                consented_at=self._clock(),
                consent_version=self._settings.recording_consent_version,
                consent_text=RECORDING_CONSENT_TEXT,
            )
        )

    def _require_consent(self, session_id: str) -> None:
        """No consent, no token -- and therefore no interview.

        Enforced at token issue rather than in the voice agent because this is
        the last point before the candidate can join a room that will be
        recorded.

        Gated on ``recording_consent_required``, which is DELIBERATELY SEPARATE
        from ``recording_enabled``: this lets recording/R2 upload/transcript
        persistence stay fully live in dev while the consent checkbox is
        temporarily off. Nothing here is deleted -- flipping
        RECORDING_CONSENT_REQUIRED back to true re-enables the gate exactly as
        it was, with no code change.
        """
        if not self._settings.recording_consent_required:
            return
        if self._consent_repo is None:
            raise VoiceRoomNotReadyError(
                "This interview is recorded but consent storage is not configured."
            )
        if self._consent_repo.get_for_session(session_id) is None:
            raise VoiceConsentRequiredError(
                "Recording consent is required before joining this interview."
            )

    def issue_room_token(self, invitation: str) -> VoiceRoomToken:
        claims = self._verified_claims(invitation)
        item = self._queue_repo.get_item(claims.item_id)
        if (
            item.queue_id != claims.queue_id
            or item.candidate_id != claims.candidate_id
            or item.interview_session_id != claims.session_id
            or claims.room_name != room_name_for_session(claims.session_id)
        ):
            raise InvalidVoiceInviteError("The voice invitation no longer matches this interview.")
        if item.status not in IN_FLIGHT_QUEUE_ITEM_STATUSES:
            raise VoiceRoomNotReadyError("This voice interview is no longer active.")
        self._require_consent(claims.session_id)
        candidate = self._candidate_repo.get(claims.candidate_id)
        position = self._position_repo.get(candidate.position_id)
        persona = resolve_voice_persona(
            self._agent_config_repo,
            position.owner_id,
            position.id,
            position.company_name,
        )
        token = self._gateway.create_participant_token(
            room_name=claims.room_name,
            participant_identity=f"candidate-{claims.candidate_id}",
            participant_name=candidate.full_name,
            ttl_seconds=self._settings.voice_token_ttl_seconds,
        )
        return VoiceRoomToken(
            **token.__dict__,
            agent_name=persona.agent_name,
            company_name=persona.company_name,
        )


    def _verified_claims(self, invitation: str) -> VoiceInviteClaims:
        claims = self._signer.verify(invitation)
        if int(self._clock().timestamp()) >= claims.exp:
            raise ExpiredVoiceInviteError("The voice invitation has expired.")
        return claims
