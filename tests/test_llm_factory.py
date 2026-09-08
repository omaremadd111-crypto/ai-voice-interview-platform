"""Provider factory behavior, including explicit real-mode failures."""
import pytest

from config.settings import Settings
from services.llm.base import LLMConfigurationError
from services.llm.factory import get_llm_service
from services.llm.mistral_service import DEFAULT_MISTRAL_MODEL, MistralLLMService
from services.llm.mock.mock_service import MockLLMService
from services.llm.nvidia_service import DEFAULT_NVIDIA_MODEL, NvidiaLLMService
from services.llm.openrouter_service import DEFAULT_OPENROUTER_MODEL, OpenRouterLLMService


def test_factory_returns_mock_when_mock_mode_true() -> None:
    settings = Settings(
        mock_mode=True,
        llm_provider="mistral",
        mistral_api_key="unit-test-key",
    )
    service = get_llm_service(settings)
    assert isinstance(service, MockLLMService)
    assert service.is_mock is True
    assert service.provider_name == "mock"


def test_factory_selects_mistral_in_explicit_real_mode() -> None:
    settings = Settings(
        mock_mode=False,
        llm_provider="mistral",
        mistral_api_key="unit-test-key",
        mistral_model="mistral-test-model",
    )
    service = get_llm_service(settings)
    assert isinstance(service, MistralLLMService)
    assert service.is_mock is False
    assert service.provider_name == "mistral"
    assert service.model == "mistral-test-model"


def test_factory_uses_documented_default_mistral_model() -> None:
    settings = Settings(
        mock_mode=False,
        llm_provider="mistral",
        mistral_api_key="unit-test-key",
    )
    service = get_llm_service(settings)
    assert isinstance(service, MistralLLMService)
    assert service.model == DEFAULT_MISTRAL_MODEL


def test_factory_rejects_missing_mistral_key_without_mock_fallback() -> None:
    settings = Settings(mock_mode=False, llm_provider="mistral", mistral_api_key=None)
    with pytest.raises(LLMConfigurationError, match="MISTRAL_API_KEY"):
        get_llm_service(settings)


def test_factory_selects_openrouter_in_explicit_real_mode() -> None:
    settings = Settings(
        mock_mode=False,
        llm_provider="openrouter",
        openrouter_api_key="unit-test-key",
        openrouter_model="some-other/model:free",
    )
    service = get_llm_service(settings)
    assert isinstance(service, OpenRouterLLMService)
    assert service.is_mock is False
    assert service.provider_name == "openrouter"
    assert service.model == "some-other/model:free"


def test_factory_uses_documented_default_openrouter_model() -> None:
    settings = Settings(
        mock_mode=False,
        llm_provider="openrouter",
        openrouter_api_key="unit-test-key",
    )
    service = get_llm_service(settings)
    assert isinstance(service, OpenRouterLLMService)
    assert service.model == DEFAULT_OPENROUTER_MODEL
    assert service.model == "nvidia/nemotron-3.5-lightning:free"


def test_factory_rejects_missing_openrouter_key_without_mock_fallback() -> None:
    settings = Settings(mock_mode=False, llm_provider="openrouter", openrouter_api_key=None)
    with pytest.raises(LLMConfigurationError, match="OPENROUTER_API_KEY"):
        get_llm_service(settings)


def test_factory_returns_mock_for_openrouter_when_mock_mode_true() -> None:
    """Mock Mode short-circuits before provider selection: choosing
    openrouter with no key at all must still work end-to-end in Mock Mode."""
    settings = Settings(mock_mode=True, llm_provider="openrouter", openrouter_api_key=None)
    service = get_llm_service(settings)
    assert isinstance(service, MockLLMService)
    assert service.is_mock is True


def test_factory_selects_nvidia_in_explicit_real_mode() -> None:
    settings = Settings(
        mock_mode=False,
        llm_provider="nvidia",
        nvidia_api_key="unit-test-key",
        nvidia_model="nvidia/some-other-model",
    )
    service = get_llm_service(settings)
    assert isinstance(service, NvidiaLLMService)
    assert service.is_mock is False
    assert service.provider_name == "nvidia"
    assert service.model == "nvidia/some-other-model"


def test_factory_uses_documented_default_nvidia_model() -> None:
    settings = Settings(
        mock_mode=False,
        llm_provider="nvidia",
        nvidia_api_key="unit-test-key",
    )
    service = get_llm_service(settings)
    assert isinstance(service, NvidiaLLMService)
    assert service.model == DEFAULT_NVIDIA_MODEL
    assert service.model == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_factory_rejects_missing_nvidia_key_without_mock_fallback() -> None:
    settings = Settings(mock_mode=False, llm_provider="nvidia", nvidia_api_key=None)
    with pytest.raises(LLMConfigurationError, match="NVIDIA_API_KEY"):
        get_llm_service(settings)


def test_factory_returns_mock_for_nvidia_when_mock_mode_true() -> None:
    """Mock Mode short-circuits before provider selection: choosing nvidia
    with no key at all must still work end-to-end in Mock Mode."""
    settings = Settings(mock_mode=True, llm_provider="nvidia", nvidia_api_key=None)
    service = get_llm_service(settings)
    assert isinstance(service, MockLLMService)
    assert service.is_mock is True


def test_factory_keeps_anthropic_unimplemented() -> None:
    settings = Settings(
        mock_mode=False,
        llm_provider="anthropic",
        anthropic_api_key="unit-test-key",
    )
    with pytest.raises(LLMConfigurationError, match="not implemented"):
        get_llm_service(settings)


def test_factory_default_settings_produce_mock() -> None:
    service = get_llm_service(Settings())
    assert isinstance(service, MockLLMService)
