"""Selects and constructs the configured LLMService. No provider SDK imports here."""
from config.settings import Settings
from services.llm.base import LLMConfigurationError, LLMService
from services.llm.mistral_service import MistralLLMService
from services.llm.mock.mock_service import MockLLMService
from services.llm.nvidia_service import NvidiaLLMService
from services.llm.openrouter_service import OpenRouterLLMService


def get_llm_service(settings: Settings) -> LLMService:
    if settings.mock_mode:
        return MockLLMService()

    if settings.llm_provider == "mistral":
        if not settings.mistral_api_key:
            raise LLMConfigurationError(
                "MISTRAL_API_KEY is required when MOCK_MODE=false and LLM_PROVIDER=mistral."
            )
        return MistralLLMService(
            api_key=settings.mistral_api_key,
            model=settings.mistral_model,
        )

    if settings.llm_provider == "openrouter":
        if not settings.openrouter_api_key:
            raise LLMConfigurationError(
                "OPENROUTER_API_KEY is required when MOCK_MODE=false and "
                "LLM_PROVIDER=openrouter."
            )
        return OpenRouterLLMService(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
        )

    if settings.llm_provider == "nvidia":
        if not settings.nvidia_api_key:
            raise LLMConfigurationError(
                "NVIDIA_API_KEY is required when MOCK_MODE=false and LLM_PROVIDER=nvidia."
            )
        return NvidiaLLMService(
            api_key=settings.nvidia_api_key,
            model=settings.nvidia_model,
        )

    if settings.llm_provider == "anthropic":
        raise LLMConfigurationError(
            "The Anthropic provider is not implemented. Use LLM_PROVIDER=mistral or "
            "set MOCK_MODE=true."
        )

    raise LLMConfigurationError(f"Unsupported llm_provider '{settings.llm_provider}'")
