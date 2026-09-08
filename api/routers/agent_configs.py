"""Agent config endpoints: thin transport only. Ownership enforcement lives in
application/agent_config_service.py. config is never read by scoring/evaluation
code -- see that service's module docstring."""
from fastapi import APIRouter, Depends

from api.dependencies import get_agent_config_service, get_current_user
from api.schemas.agent_config import AgentConfigCreateRequest, AgentConfigResponse, AgentConfigUpdateRequest
from application.agent_config_service import AgentConfigService
from models.agent_persona import default_persona
from models.platform import AgentConfigRecord, HRUser

router = APIRouter(prefix="/api/v1/agent-configs", tags=["agent-configs"])


@router.get("/persona-template")
def get_persona_template(
    agent_name: str = "Aimy",
    company_name: str = "Your Company",
    _current_user: HRUser = Depends(get_current_user),
) -> dict:
    """A valid starting persona for the voice agent, so HR edits a working example
    rather than assembling one that must satisfy the AI-disclosure rule."""
    return default_persona(agent_name=agent_name, company_name=company_name).model_dump(mode="json")


@router.post("", response_model=AgentConfigResponse, status_code=201)
def create_agent_config(
    payload: AgentConfigCreateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: AgentConfigService = Depends(get_agent_config_service),
) -> AgentConfigResponse:
    config = service.create(current_user, **payload.model_dump())
    return _to_response(config)


@router.get("", response_model=list[AgentConfigResponse])
def list_agent_configs(
    current_user: HRUser = Depends(get_current_user),
    service: AgentConfigService = Depends(get_agent_config_service),
) -> list[AgentConfigResponse]:
    return [_to_response(c) for c in service.list(current_user)]


@router.get("/{config_id}", response_model=AgentConfigResponse)
def get_agent_config(
    config_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: AgentConfigService = Depends(get_agent_config_service),
) -> AgentConfigResponse:
    return _to_response(service.get(current_user, config_id))


@router.patch("/{config_id}", response_model=AgentConfigResponse)
def update_agent_config(
    config_id: int,
    payload: AgentConfigUpdateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: AgentConfigService = Depends(get_agent_config_service),
) -> AgentConfigResponse:
    updates = payload.model_dump(exclude_unset=True)
    return _to_response(service.update(current_user, config_id, updates))


def _to_response(config: AgentConfigRecord) -> AgentConfigResponse:
    return AgentConfigResponse(
        id=config.id,
        owner_id=config.owner_id,
        position_id=config.position_id,
        name=config.name,
        config=config.config,
        is_active=config.is_active,
    )
