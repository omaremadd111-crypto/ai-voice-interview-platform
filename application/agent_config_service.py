"""Agent config use-case logic: ownership-enforced CRUD.

Deliberately has no dependency on evaluation/scoring code: agent_configs holds
tone/persona/voice settings only, and services/scoring.py never reads this
table, so nothing here can influence a candidate score or screening outcome.
"""
from pydantic import ValidationError

from application.auth_service import ForbiddenError
from application.position_service import PositionService
from models.agent_persona import VoiceAgentPersona
from models.platform import AgentConfigRecord, HRUser
from services.db.agent_configs import AgentConfigRepository


class InvalidPersonaError(Exception):
    """Raised when a submitted voice-agent persona fails validation -- including the
    AI-disclosure requirement and the ban on emotion/accent/personality/biometric
    settings."""


def validate_persona(config: dict) -> dict:
    """Validate a persona config and return it normalized.

    A config is treated as a voice persona when it carries the required identity
    fields. Anything else passes through untouched, so pre-persona configs and
    unrelated settings blobs keep working.
    """
    if not {"agent_name", "company_name"} <= set(config):
        return config
    try:
        return VoiceAgentPersona.model_validate(config).model_dump(mode="json")
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or 'config'}: {error['msg']}"
            for error in exc.errors()
        )
        raise InvalidPersonaError(problems) from exc


class AgentConfigService:
    def __init__(self, config_repo: AgentConfigRepository, position_service: PositionService) -> None:
        self._config_repo = config_repo
        self._position_service = position_service

    def create(
        self,
        owner: HRUser,
        *,
        name: str,
        position_id: int | None = None,
        config: dict | None = None,
        is_active: bool = True,
    ) -> AgentConfigRecord:
        if position_id is not None:
            self._position_service.get(owner, position_id)  # raises if position isn't the caller's
        record = AgentConfigRecord(
            owner_id=owner.id,
            position_id=position_id,
            name=name,
            config=validate_persona(config or {}),
            is_active=is_active,
        )
        return self._config_repo.create(record)

    def get(self, owner: HRUser, config_id: int) -> AgentConfigRecord:
        config = self._config_repo.get(config_id)
        _require_owner(owner, config)
        return config

    def list(self, owner: HRUser) -> list[AgentConfigRecord]:
        return self._config_repo.list(owner_id=owner.id)

    def update(self, owner: HRUser, config_id: int, updates: dict) -> AgentConfigRecord:
        existing = self.get(owner, config_id)
        if "config" in updates and updates["config"] is not None:
            updates = {**updates, "config": validate_persona(updates["config"])}
        updated = existing.model_copy(update=updates)
        return self._config_repo.update(updated)

    def delete(self, owner: HRUser, config_id: int) -> None:
        self.get(owner, config_id)
        self._config_repo.delete(config_id)


def _require_owner(owner: HRUser, config: AgentConfigRecord) -> None:
    if config.owner_id != owner.id:
        raise ForbiddenError("You do not have access to this agent config")
