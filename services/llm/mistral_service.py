"""Official Mistral SDK adapter for the provider-neutral LLMService contract.

The optional SDK is imported lazily only when a real request is made. This keeps
Mock Mode dependency-free while allowing the factory to construct a real-provider
service without silently falling back when configuration is incomplete.
"""
from __future__ import annotations

import json
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from services.llm.base import (
    LLMConfigurationError,
    LLMProviderError,
    LLMRequest,
    LLMService,
    LLMStructuredError,
    SUPPORTED_TASKS,
    UnsupportedTaskError,
)

T = TypeVar("T", bound=BaseModel)

# Mistral Small 4, GA model v26.03. The official model card documents both
# chat completions and structured outputs for this stable API id.
DEFAULT_MISTRAL_MODEL = "mistral-small-2603"
_MAX_REPAIR_ERROR_CHARS = 2000
_MAX_REPAIR_TOKENS = 8192


class _ChatAPI(Protocol):
    def parse(self, response_format: type[T], **kwargs: Any) -> Any: ...

    def complete(self, **kwargs: Any) -> Any: ...


class _MistralClient(Protocol):
    chat: _ChatAPI


class _MissingStructuredOutputError(Exception):
    """Internal signal that a provider response contained no parsed object."""


class MistralLLMService(LLMService):
    provider_name = "mistral"
    is_mock = False

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        client: _MistralClient | None = None,
        api_error_types: tuple[type[Exception], ...] | None = None,
    ) -> None:
        normalized_key = api_key.strip() if api_key else ""
        if not normalized_key:
            raise LLMConfigurationError("MISTRAL_API_KEY must not be blank.")
        self._api_key = normalized_key
        self.model = model.strip() if model and model.strip() else DEFAULT_MISTRAL_MODEL
        self._client = client
        self._api_error_types = api_error_types

    def generate_structured(self, request: LLMRequest, schema: type[T]) -> T:
        self._require_supported_task(request.task)
        messages = self._messages(request)
        for attempt in range(2):
            try:
                max_tokens = (
                    request.max_tokens
                    if attempt == 0
                    else min(request.max_tokens * 2, _MAX_REPAIR_TOKENS)
                )
                response = self._parse(request, schema, messages, max_tokens=max_tokens)
                return self._validated_parsed_output(response, schema)
            except (ValidationError, json.JSONDecodeError, TypeError, _MissingStructuredOutputError) as exc:
                if attempt == 0:
                    messages = self._repair_messages(messages, exc)
                    continue
                raise LLMStructuredError(
                    f"Mistral returned invalid structured output for task '{request.task}' "
                    "after one repair attempt."
                ) from exc
        raise LLMStructuredError("Mistral structured-output repair loop ended unexpectedly.")

    def generate_text(self, request: LLMRequest) -> str:
        self._require_supported_task(request.task)
        client, api_error_types = self._client_and_errors()
        try:
            response = client.chat.complete(
                model=self.model,
                messages=self._messages(request),
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
        except api_error_types as exc:
            raise self._provider_error(exc) from exc
        return self._text_content(response)

    def _parse(
        self,
        request: LLMRequest,
        schema: type[T],
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
    ) -> Any:
        client, api_error_types = self._client_and_errors()
        try:
            return client.chat.parse(
                response_format=schema,
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=request.temperature,
            )
        except api_error_types as exc:
            raise self._provider_error(exc) from exc

    def _client_and_errors(
        self,
    ) -> tuple[_MistralClient, tuple[type[Exception], ...]]:
        if self._client is None:
            self._client, self._api_error_types = _load_official_client(self._api_key)
        return self._client, self._api_error_types or ()

    @staticmethod
    def _messages(request: LLMRequest) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ]

    @staticmethod
    def _repair_messages(
        messages: list[dict[str, str]],
        error: Exception,
    ) -> list[dict[str, str]]:
        validation_text = str(error)[:_MAX_REPAIR_ERROR_CHARS]
        repair_prompt = (
            "Your previous structured response failed schema validation. Correct it and "
            "return only a complete object that matches the requested schema. Keep the "
            "content concise, do not repeat items, and ensure the JSON finishes within the "
            "response limit. Validation error:\n"
            f"{validation_text}"
        )
        return [*messages, {"role": "user", "content": repair_prompt}]

    @staticmethod
    def _validated_parsed_output(response: Any, schema: type[T]) -> T:
        choices = getattr(response, "choices", None)
        if not choices:
            raise _MissingStructuredOutputError("Mistral response contained no choices.")
        message = getattr(choices[0], "message", None)
        parsed = getattr(message, "parsed", None)
        if parsed is None:
            raise _MissingStructuredOutputError("Mistral response contained no parsed output.")
        return schema.model_validate(parsed)

    @staticmethod
    def _text_content(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMProviderError("Mistral returned no completion choices.")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [_content_part_text(part) for part in content]
            text = "".join(part for part in parts if part)
            if text:
                return text
        raise LLMProviderError("Mistral returned an unsupported text response.")

    @staticmethod
    def _require_supported_task(task: str) -> None:
        if task not in SUPPORTED_TASKS:
            raise UnsupportedTaskError(f"Mistral does not support task '{task}'.")

    @staticmethod
    def _provider_error(error: Exception) -> LLMProviderError:
        status_code = getattr(error, "status_code", None)
        suffix = f" (HTTP {status_code})" if isinstance(status_code, int) else ""
        return LLMProviderError(f"Mistral API request failed{suffix}.")


def _load_official_client(
    api_key: str,
) -> tuple[_MistralClient, tuple[type[Exception], ...]]:
    try:
        import httpx
        from mistralai.client import Mistral, errors
    except ImportError as exc:
        raise LLMConfigurationError(
            "The Mistral SDK is not installed. Install requirements-llm.txt to use "
            "LLM_PROVIDER=mistral."
        ) from exc
    return Mistral(api_key=api_key), (errors.MistralError, httpx.RequestError)


def _content_part_text(part: Any) -> str:
    if isinstance(part, dict):
        value = part.get("text")
    else:
        value = getattr(part, "text", None)
    return value if isinstance(value, str) else ""
