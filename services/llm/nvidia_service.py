"""Official OpenAI SDK adapter for NVIDIA NIM's OpenAI-compatible API.

NVIDIA NIM (integrate.api.nvidia.com) serves hosted models, including
Nemotron, through the same OpenAI-compatible chat completions shape
OpenRouter uses -- see services/llm/openrouter_service.py, which this mirrors
closely. Nemotron does not natively guarantee strict JSON-schema-constrained
output, so structured output uses the same provider-agnostic schema-in-prompt
+ client-side validation helpers (services/llm/structured.py), combined with
a best-effort JSON response_format hint and a repair-retry loop mirroring
MistralLLMService/OpenRouterLLMService.

Every request disables Nemotron's "thinking" mode via
extra_body={"chat_template_kwargs": {"enable_thinking": False}} -- the
documented switch for this model family (verified against the real endpoint).
Without it, the model spends part of its completion budget on a hidden
reasoning trace before ever writing the answer -- the same class of problem
OpenRouterLLMService already works around for routed reasoning models (see
that module's docstring), just a different provider-specific switch.

The optional SDK is imported lazily only when a real request is made. This
keeps Mock Mode dependency-free while allowing the factory to construct a
real-provider service without silently falling back when configuration is
incomplete.
"""
from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from services.llm.base import (
    LLMConfigurationError,
    LLMProviderError,
    LLMRequest,
    LLMService,
    LLMStructuredError,
    SUPPORTED_TASKS,
    UnsupportedTaskError,
)
from services.llm.structured import parse_structured_response, render_schema_instructions

T = TypeVar("T", bound=BaseModel)

_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
# Verified working against the live NIM endpoint. Concrete and pinned, never a
# bare/"-latest" alias, so a bare LLM_PROVIDER=nvidia works with no further
# tuning while staying reproducible.
DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
# Nemotron's thinking-mode switch, disabled on every request -- see the module
# docstring for why this is not optional.
_DISABLE_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}
_MAX_REPAIR_ERROR_CHARS = 2000
_MAX_REPAIR_TOKENS = 8192


class _ChatCompletionsAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _ChatAPI(Protocol):
    completions: _ChatCompletionsAPI


class _NvidiaClient(Protocol):
    chat: _ChatAPI


class NvidiaLLMService(LLMService):
    provider_name = "nvidia"
    is_mock = False

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        client: _NvidiaClient | None = None,
        api_error_types: tuple[type[Exception], ...] | None = None,
    ) -> None:
        normalized_key = api_key.strip() if api_key else ""
        if not normalized_key:
            raise LLMConfigurationError("NVIDIA_API_KEY must not be blank.")
        self._api_key = normalized_key
        self.model = model.strip() if model and model.strip() else DEFAULT_NVIDIA_MODEL
        self._client = client
        self._api_error_types = api_error_types

    def generate_structured(self, request: LLMRequest, schema: type[T]) -> T:
        self._require_supported_task(request.task)
        client, api_error_types = self._client_and_errors()
        system_with_schema = f"{request.system}\n\n{render_schema_instructions(schema)}"
        messages = self._messages(request, system=system_with_schema)
        for attempt in range(2):
            max_tokens = (
                request.max_tokens
                if attempt == 0
                else min(request.max_tokens * 2, _MAX_REPAIR_TOKENS)
            )
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=request.temperature,
                    # Best-effort hint: not every NIM-hosted model honours this.
                    # parse_structured_response below is what actually enforces
                    # the schema, so this is safe to include either way.
                    response_format={"type": "json_object"},
                    extra_body=_DISABLE_THINKING,
                )
            except api_error_types as exc:
                raise self._provider_error(exc) from exc
            raw_text = self._text_content(response)
            try:
                return parse_structured_response(raw_text, schema)
            except LLMStructuredError as exc:
                if attempt == 0:
                    messages = self._repair_messages(messages, exc)
                    continue
                raise LLMStructuredError(
                    f"NVIDIA returned invalid structured output for task "
                    f"'{request.task}' after one repair attempt."
                ) from exc
        raise LLMStructuredError("NVIDIA structured-output repair loop ended unexpectedly.")

    def generate_text(self, request: LLMRequest) -> str:
        self._require_supported_task(request.task)
        client, api_error_types = self._client_and_errors()
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=self._messages(request),
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                extra_body=_DISABLE_THINKING,
            )
        except api_error_types as exc:
            raise self._provider_error(exc) from exc
        return self._text_content(response)

    def _client_and_errors(
        self,
    ) -> tuple[_NvidiaClient, tuple[type[Exception], ...]]:
        if self._client is None:
            self._client, self._api_error_types = _load_official_client(self._api_key)
        return self._client, self._api_error_types or ()

    @staticmethod
    def _messages(request: LLMRequest, *, system: str | None = None) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system if system is not None else request.system},
            {"role": "user", "content": request.user},
        ]

    @staticmethod
    def _repair_messages(
        messages: list[dict[str, str]],
        error: Exception,
    ) -> list[dict[str, str]]:
        validation_text = str(error)[:_MAX_REPAIR_ERROR_CHARS]
        repair_prompt = (
            "Your previous response failed schema validation. Correct it and return "
            "only a single complete JSON object that matches the requested schema, "
            "with no text outside the JSON object. Keep the content concise, do not "
            "repeat items, and ensure the JSON finishes within the response limit. "
            "Validation error:\n"
            f"{validation_text}"
        )
        return [*messages, {"role": "user", "content": repair_prompt}]

    @staticmethod
    def _text_content(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMProviderError("NVIDIA returned no completion choices.")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            return content
        if isinstance(content, list):
            parts = [_content_part_text(part) for part in content]
            text = "".join(part for part in parts if part)
            if text:
                return text
        raise LLMProviderError("NVIDIA returned an unsupported text response.")

    @staticmethod
    def _require_supported_task(task: str) -> None:
        if task not in SUPPORTED_TASKS:
            raise UnsupportedTaskError(f"NVIDIA does not support task '{task}'.")

    @staticmethod
    def _provider_error(error: Exception) -> LLMProviderError:
        status_code = getattr(error, "status_code", None)
        suffix = f" (HTTP {status_code})" if isinstance(status_code, int) else ""
        return LLMProviderError(f"NVIDIA API request failed{suffix}.")


def _load_official_client(
    api_key: str,
) -> tuple[_NvidiaClient, tuple[type[Exception], ...]]:
    try:
        from openai import OpenAI, OpenAIError
    except ImportError as exc:
        raise LLMConfigurationError(
            "The OpenAI SDK is not installed. Install requirements-llm.txt to use "
            "LLM_PROVIDER=nvidia."
        ) from exc
    client = OpenAI(api_key=api_key, base_url=_NVIDIA_BASE_URL)
    return client, (OpenAIError,)


def _content_part_text(part: Any) -> str:
    if isinstance(part, dict):
        value = part.get("text")
    else:
        value = getattr(part, "text", None)
    return value if isinstance(value, str) else ""
