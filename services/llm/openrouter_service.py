"""Official OpenAI SDK adapter for OpenRouter's OpenAI-compatible API.

OpenRouter proxies many third-party models, most of which do not natively
guarantee strict JSON-schema-constrained output the way Mistral's official
client does (services/llm/mistral_service.py uses Mistral's own `chat.parse`).
Structured output here therefore uses the provider-agnostic schema-in-prompt +
client-side validation helpers already scaffolded in services/llm/structured.py
for exactly this situation, combined with a best-effort JSON response_format
hint and a repair-retry loop mirroring MistralLLMService -- a design that works
uniformly regardless of whether the routed model has genuine structured-output
support.

Every request disables OpenRouter's unified reasoning control
(`extra_body={"reasoning": {"enabled": False}}`). Real benchmarking against
this project's actual VoiceIntentClassifier/Interviewer prompts showed reasoning
models routed through OpenRouter spend their entire completion budget on a
hidden `message.reasoning` field before ever writing `message.content`,
reliably producing empty output at the classifier's real ~200-token budget.
Disabling reasoning is what makes free-tier models usable on this project's
live conversational path at all; every model that passed the real-workload
benchmark did so only with reasoning off.

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

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Selected from a real-workload latency/reliability benchmark across the free
# models available on OpenRouter (intent classification + follow-up
# generation, reasoning disabled): 5/5 success on both workloads, ~1.6-1.9s
# median latency, and no announced end-of-life -- unlike the model previously
# defaulted here (dots-studio/dots-3-note-preview:free, which is also a valid
# choice but is scheduled to be removed from OpenRouter on 2026-09-30).
# Concrete and pinned (never a bare/"-latest" alias) so a bare
# LLM_PROVIDER=openrouter works with no further tuning while staying
# reproducible.
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3.5-lightning:free"
# OpenRouter's unified reasoning control, disabled on every request -- see the
# module docstring for why this is not optional.
_DISABLE_REASONING = {"reasoning": {"enabled": False}}
_MAX_REPAIR_ERROR_CHARS = 2000
_MAX_REPAIR_TOKENS = 8192


class _ChatCompletionsAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _ChatAPI(Protocol):
    completions: _ChatCompletionsAPI


class _OpenRouterClient(Protocol):
    chat: _ChatAPI


class OpenRouterLLMService(LLMService):
    provider_name = "openrouter"
    is_mock = False

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        client: _OpenRouterClient | None = None,
        api_error_types: tuple[type[Exception], ...] | None = None,
    ) -> None:
        normalized_key = api_key.strip() if api_key else ""
        if not normalized_key:
            raise LLMConfigurationError("OPENROUTER_API_KEY must not be blank.")
        self._api_key = normalized_key
        self.model = model.strip() if model and model.strip() else DEFAULT_OPENROUTER_MODEL
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
                    # Best-effort hint: widely but not universally honoured by
                    # models routed through OpenRouter. parse_structured_response
                    # below is what actually enforces the schema, so this is
                    # safe to include even for a model that ignores it.
                    response_format={"type": "json_object"},
                    extra_body=_DISABLE_REASONING,
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
                    f"OpenRouter returned invalid structured output for task "
                    f"'{request.task}' after one repair attempt."
                ) from exc
        raise LLMStructuredError("OpenRouter structured-output repair loop ended unexpectedly.")

    def generate_text(self, request: LLMRequest) -> str:
        self._require_supported_task(request.task)
        client, api_error_types = self._client_and_errors()
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=self._messages(request),
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                extra_body=_DISABLE_REASONING,
            )
        except api_error_types as exc:
            raise self._provider_error(exc) from exc
        return self._text_content(response)

    def _client_and_errors(
        self,
    ) -> tuple[_OpenRouterClient, tuple[type[Exception], ...]]:
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
            raise LLMProviderError("OpenRouter returned no completion choices.")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            return content
        if isinstance(content, list):
            parts = [_content_part_text(part) for part in content]
            text = "".join(part for part in parts if part)
            if text:
                return text
        raise LLMProviderError("OpenRouter returned an unsupported text response.")

    @staticmethod
    def _require_supported_task(task: str) -> None:
        if task not in SUPPORTED_TASKS:
            raise UnsupportedTaskError(f"OpenRouter does not support task '{task}'.")

    @staticmethod
    def _provider_error(error: Exception) -> LLMProviderError:
        status_code = getattr(error, "status_code", None)
        suffix = f" (HTTP {status_code})" if isinstance(status_code, int) else ""
        return LLMProviderError(f"OpenRouter API request failed{suffix}.")


def _load_official_client(
    api_key: str,
) -> tuple[_OpenRouterClient, tuple[type[Exception], ...]]:
    try:
        from openai import OpenAI, OpenAIError
    except ImportError as exc:
        raise LLMConfigurationError(
            "The OpenAI SDK is not installed. Install requirements-llm.txt to use "
            "LLM_PROVIDER=openrouter."
        ) from exc
    client = OpenAI(api_key=api_key, base_url=_OPENROUTER_BASE_URL)
    return client, (OpenAIError,)


def _content_part_text(part: Any) -> str:
    if isinstance(part, dict):
        value = part.get("text")
    else:
        value = getattr(part, "text", None)
    return value if isinstance(value, str) else ""
