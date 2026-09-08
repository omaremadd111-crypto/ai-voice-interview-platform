"""Provider-agnostic LLM request/response contracts. No provider SDK imports here."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

SUPPORTED_TASKS = frozenset({
    "job_analysis",
    "candidate_analysis",
    "fit_analysis",
    "interview_planning",
    "follow_up",
    "evaluation",
    "voice_intent",
})


class LLMError(Exception):
    """Base class for all LLM-layer errors."""


class UnsupportedTaskError(LLMError):
    """Raised when an LLMRequest.task has no handler."""


class LLMStructuredError(LLMError):
    """Raised when a structured response cannot be produced or validated."""


class LLMConfigurationError(LLMError):
    """Raised when an explicitly selected provider is not usable as configured."""


class LLMProviderError(LLMError):
    """Raised when a real provider request fails."""


@dataclass(frozen=True)
class LLMRequest:
    task: str
    system: str
    user: str
    context: dict[str, Any]
    max_tokens: int = 2000
    temperature: float = 0.2

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            raise ValueError("LLMRequest.task must not be blank")


class LLMService(ABC):
    provider_name: str
    is_mock: bool

    @abstractmethod
    def generate_structured(self, request: LLMRequest, schema: type[T]) -> T: ...

    @abstractmethod
    def generate_text(self, request: LLMRequest) -> str: ...
