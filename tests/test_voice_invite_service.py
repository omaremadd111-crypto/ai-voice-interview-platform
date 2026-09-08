from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from application.voice_invite_service import (
    ExpiredVoiceInviteError,
    InvalidVoiceInviteError,
    VoiceInviteClaims,
    VoiceInviteService,
    VoiceInviteSigner,
)
from config.settings import Settings
from models.common import QueueItemStatus
from models.platform import HRUser
from services.livekit.base import LiveKitToken


class FakeGateway:
    def close_room(self, room_name: str) -> None:
        return None

    def create_participant_token(self, **kwargs) -> LiveKitToken:
        return LiveKitToken(
            token="room-jwt",
            server_url="wss://example.livekit.cloud",
            room_name=kwargs["room_name"],
            participant_identity=kwargs["participant_identity"],
            participant_name=kwargs["participant_name"],
        )


def test_signer_rejects_tampered_invitation() -> None:
    signer = VoiceInviteSigner("test-secret")
    claims = VoiceInviteClaims(
        queue_id=1, item_id=2, candidate_id=3, session_id="s", room_name="hr-screen-s", exp=10,
    )
    signed = signer.sign(claims)

    assert signer.verify(signed) == claims
    with pytest.raises(InvalidVoiceInviteError):
        signer.verify(f"{signed[:-1]}x")


def test_invitation_is_owner_checked_and_exchanged_for_candidate_token() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    item = SimpleNamespace(
        id=2,
        queue_id=1,
        candidate_id=3,
        status=QueueItemStatus.IN_PROGRESS,
        interview_session_id="session-4",
    )
    queue_service = SimpleNamespace(get=lambda owner, queue_id: object())
    repo = SimpleNamespace(get_item=lambda item_id: item)
    candidate_repo = SimpleNamespace(
        get=lambda candidate_id: SimpleNamespace(full_name="Sam Taylor", position_id=3)
    )
    position_repo = SimpleNamespace(
        get=lambda position_id: SimpleNamespace(
            id=3, owner_id=9, company_name="Acme",
        )
    )
    config_repo = SimpleNamespace(list=lambda **kwargs: [])
    service = VoiceInviteService(
        queue_service,
        repo,
        candidate_repo,
        position_repo,
        config_repo,
        FakeGateway(),
        VoiceInviteSigner("test-secret"),
        Settings(voice_public_base_url="https://interviews.example", voice_join_timeout_seconds=60),
        clock=lambda: now,
    )

    invitation = service.create_invitation(HRUser(id=9, email="x@y.z", password_hash="h", full_name="HR"), 1, 2)
    token = service.issue_room_token(invitation.invite_url.rsplit("/", 1)[-1])

    assert invitation.invite_url.startswith("https://interviews.example/voice/")
    assert token.token == "room-jwt"
    assert token.room_name == "hr-screen-session-4"
    assert token.participant_identity == "candidate-3"
    assert token.agent_name == "Aimy"
    assert token.company_name == "Acme"


def test_expired_invitation_cannot_issue_room_token() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    signer = VoiceInviteSigner("test-secret")
    expired = signer.sign(
        VoiceInviteClaims(
            queue_id=1,
            item_id=2,
            candidate_id=3,
            session_id="s",
            room_name="hr-screen-s",
            exp=int((now - timedelta(seconds=1)).timestamp()),
        )
    )
    service = VoiceInviteService(
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
        SimpleNamespace(), FakeGateway(), signer, Settings(), clock=lambda: now,
    )

    with pytest.raises(ExpiredVoiceInviteError):
        service.issue_room_token(expired)
