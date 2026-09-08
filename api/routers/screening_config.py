"""Screening template / automation-settings endpoints: thin transport only.

Delegates entirely to application/position_publishing_service.py. The only
transport-layer work here is building `public_url` from the position's
public_slug and VOICE_PUBLIC_BASE_URL -- string formatting, not a decision,
exactly like VoiceInviteService already builds invite_url from the same
setting for the candidate-facing voice link.
"""
from fastapi import APIRouter, Depends

from api.dependencies import get_current_user, get_position_publishing_service, get_settings
from api.schemas.screening_config import ScreeningConfigResponse, ScreeningSettingsUpdateRequest
from application.position_publishing_service import PositionPublishingService, ScreeningSetup
from config.settings import Settings
from models.platform import HRUser

router = APIRouter(prefix="/api/v1/positions", tags=["screening-config"])


@router.get("/{position_id}/screening-config", response_model=ScreeningConfigResponse)
def get_screening_config(
    position_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: PositionPublishingService = Depends(get_position_publishing_service),
    settings: Settings = Depends(get_settings),
) -> ScreeningConfigResponse:
    setup = service.get_screening_setup(current_user, position_id)
    return _to_response(setup, settings)


@router.put("/{position_id}/screening-config", response_model=ScreeningConfigResponse)
def update_screening_config(
    position_id: int,
    payload: ScreeningSettingsUpdateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: PositionPublishingService = Depends(get_position_publishing_service),
    settings: Settings = Depends(get_settings),
) -> ScreeningConfigResponse:
    updates = payload.model_dump(exclude_unset=True)
    setup = service.update_settings(current_user, position_id, updates)
    return _to_response(setup, settings)


@router.post("/{position_id}/screening-template/approve", response_model=ScreeningConfigResponse)
def approve_screening_template(
    position_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: PositionPublishingService = Depends(get_position_publishing_service),
    settings: Settings = Depends(get_settings),
) -> ScreeningConfigResponse:
    setup = service.approve_template(current_user, position_id)
    return _to_response(setup, settings)


@router.post("/{position_id}/publish", response_model=ScreeningConfigResponse)
def publish_position(
    position_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: PositionPublishingService = Depends(get_position_publishing_service),
    settings: Settings = Depends(get_settings),
) -> ScreeningConfigResponse:
    setup = service.publish(current_user, position_id)
    return _to_response(setup, settings)


@router.post("/{position_id}/unpublish", response_model=ScreeningConfigResponse)
def unpublish_position(
    position_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: PositionPublishingService = Depends(get_position_publishing_service),
    settings: Settings = Depends(get_settings),
) -> ScreeningConfigResponse:
    setup = service.unpublish(current_user, position_id)
    return _to_response(setup, settings)


def _to_response(setup: ScreeningSetup, settings: Settings) -> ScreeningConfigResponse:
    slug = setup.position.public_slug
    public_url = f"{settings.voice_public_base_url}/jobs/{slug}" if slug else None
    return ScreeningConfigResponse(
        position_id=setup.config.position_id,
        template_status=setup.config.template_status,
        template_approved_at=setup.config.template_approved_at,
        accept_public_applications=setup.config.accept_public_applications,
        auto_parse_cv=setup.config.auto_parse_cv,
        auto_create_plan=setup.config.auto_create_plan,
        auto_create_invitation=setup.config.auto_create_invitation,
        allow_immediate_start=setup.config.allow_immediate_start,
        auto_email_invitation=setup.config.auto_email_invitation,
        allow_cv_personalization=setup.config.allow_cv_personalization,
        invitation_ttl_hours=setup.config.invitation_ttl_hours,
        reminder_offsets_hours=setup.config.reminder_offsets_hours,
        max_applications_per_day=setup.config.max_applications_per_day,
        require_phone=setup.config.require_phone,
        application_notice=setup.config.application_notice,
        public_slug=slug,
        public_url=public_url,
    )
