"""Agent config request/response schemas.

config is a free-form settings blob (tone, persona, voice preferences). It is
never read by services/scoring.py or the evaluator -- see docs/ARCHITECTURE.md: agent
customization must never influence a candidate score or screening outcome.
"""
from pydantic import BaseModel, Field


class AgentConfigCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    position_id: int | None = None
    config: dict = Field(default_factory=dict)
    is_active: bool = True


class AgentConfigUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict | None = None
    is_active: bool | None = None


class AgentConfigResponse(BaseModel):
    id: int
    owner_id: int
    position_id: int | None
    name: str
    config: dict
    is_active: bool
