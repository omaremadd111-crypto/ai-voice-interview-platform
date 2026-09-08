"""Provider-agnostic helpers for requesting and parsing schema-validated LLM output.

Real providers (Phase 10+) use render_schema_instructions to tell the model what
shape to produce, and parse_structured_response to validate what comes back --
retrying the call is that provider's own responsibility, not this module's.
"""
import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from services.llm.base import LLMStructuredError

T = TypeVar("T", bound=BaseModel)


def render_schema_instructions(schema: type[T]) -> str:
    json_schema = schema.model_json_schema()
    return (
        "Respond with a single JSON object that strictly matches this JSON Schema. "
        "Do not include any text outside the JSON object.\n\n"
        f"{json.dumps(json_schema, indent=2)}"
    )


def parse_structured_response(raw: str | dict, schema: type[T]) -> T:
    try:
        if isinstance(raw, str):
            return schema.model_validate_json(raw)
        return schema.model_validate(raw)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise LLMStructuredError(
            f"Response did not match the expected {schema.__name__} schema: {exc}"
        ) from exc
