"""Tests for LLMRequest validation and the provider-agnostic structured-output helpers."""
import pytest
from pydantic import BaseModel

from services.llm.base import LLMRequest, LLMStructuredError
from services.llm.structured import parse_structured_response, render_schema_instructions


class _Sample(BaseModel):
    name: str
    score: int


# ---- LLMRequest ----

def test_llm_request_rejects_blank_task() -> None:
    with pytest.raises(ValueError):
        LLMRequest(task="   ", system="s", user="u", context={})


def test_llm_request_is_frozen() -> None:
    request = LLMRequest(task="job_analysis", system="s", user="u", context={})
    with pytest.raises(Exception):
        request.task = "other"  # type: ignore[misc]


def test_llm_request_holds_context_dict() -> None:
    request = LLMRequest(task="job_analysis", system="s", user="u", context={"a": 1}, max_tokens=500, temperature=0.0)
    assert request.context == {"a": 1}
    assert request.max_tokens == 500
    assert request.temperature == 0.0


# ---- render_schema_instructions ----

def test_render_schema_instructions_includes_field_names() -> None:
    instructions = render_schema_instructions(_Sample)
    assert "name" in instructions
    assert "score" in instructions
    assert "JSON" in instructions


# ---- parse_structured_response ----

def test_parse_structured_response_from_json_string() -> None:
    result = parse_structured_response('{"name": "x", "score": 5}', _Sample)
    assert result == _Sample(name="x", score=5)


def test_parse_structured_response_from_dict() -> None:
    result = parse_structured_response({"name": "x", "score": 5}, _Sample)
    assert result == _Sample(name="x", score=5)


def test_parse_structured_response_rejects_malformed_json() -> None:
    with pytest.raises(LLMStructuredError):
        parse_structured_response("{not valid json", _Sample)


def test_parse_structured_response_rejects_schema_mismatch() -> None:
    with pytest.raises(LLMStructuredError):
        parse_structured_response({"name": "x"}, _Sample)  # missing required 'score'


def test_parse_structured_response_error_does_not_leak_pydantic_type() -> None:
    with pytest.raises(LLMStructuredError) as exc_info:
        parse_structured_response({"name": "x"}, _Sample)
    assert type(exc_info.value) is LLMStructuredError
