"""Mistral provider contract tests with a deterministic SDK-shaped fake."""
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from models.candidate import CandidateAnalysis, FitAnalysis
from models.evaluation import CategoryEvaluation
from models.interview import FollowUpDecision, InterviewPlan
from models.job import JobAnalysis
from services.llm.base import (
    LLMConfigurationError,
    LLMProviderError,
    LLMRequest,
    LLMStructuredError,
    UnsupportedTaskError,
)
from services.llm.mistral_service import DEFAULT_MISTRAL_MODEL, MistralLLMService


class SampleOutput(BaseModel):
    name: str
    score: int


class FakeMistralAPIError(Exception):
    status_code = 429


class FakeChatAPI:
    def __init__(
        self,
        structured_outcomes: list[Any] | None = None,
        text_outcome: str | Exception = "plain response",
    ) -> None:
        self.structured_outcomes = list(structured_outcomes or [])
        self.text_outcome = text_outcome
        self.parse_calls: list[dict[str, Any]] = []
        self.complete_calls: list[dict[str, Any]] = []

    def parse(self, response_format: type[BaseModel], **kwargs: Any) -> Any:
        self.parse_calls.append({"response_format": response_format, **kwargs})
        outcome = self.structured_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        parsed = response_format.model_validate(outcome)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]
        )

    def complete(self, **kwargs: Any) -> Any:
        self.complete_calls.append(kwargs)
        if isinstance(self.text_outcome, Exception):
            raise self.text_outcome
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.text_outcome))]
        )


class FakeMistralClient:
    def __init__(self, chat: FakeChatAPI) -> None:
        self.chat = chat


TASK_CASES = [
    (
        "job_analysis",
        JobAnalysis,
        {
            "job_title": "Junior AI Engineer",
            "experience_level": "Junior",
            "required_skills": ["Python"],
            "nice_to_have_skills": [],
            "responsibilities": ["Build APIs"],
            "technical_topics": ["Python"],
            "behavioral_competencies": ["Communication"],
            "role_summary": "Build reliable AI services.",
        },
    ),
    (
        "candidate_analysis",
        CandidateAnalysis,
        {
            "skills": ["Python"],
            "technologies": ["Python"],
            "experience": ["Built an API"],
            "projects": ["RAG service"],
            "education": [],
            "important_cv_claims": ["Built a RAG service"],
            "relevant_experience": ["Built an API"],
            "unclear_claims_to_validate": ["Built a RAG service"],
        },
    ),
    (
        "fit_analysis",
        FitAnalysis,
        {
            "strong_alignment_areas": ["Python"],
            "relevant_candidate_experience": ["Built an API"],
            "important_job_requirements": ["Python"],
            "skills_requiring_validation": ["RAG"],
            "missing_information": ["Deployment"],
            "questions_to_investigate": ["How was retrieval evaluated?"],
        },
    ),
    (
        "interview_planning",
        InterviewPlan,
        {
            "questions": [{
                "id": "q1",
                "category": "technical",
                "question": "Explain your Python API work.",
                "purpose": "Validate relevant technical experience.",
                "expected_topics": ["Python"],
                "difficulty": "junior",
                "follow_up_allowed": True,
            }],
        },
    ),
    (
        "follow_up",
        FollowUpDecision,
        {
            "should_ask": True,
            "follow_up_question": "How did you test it?",
            "reason": "Testing evidence is unclear.",
        },
    ),
    (
        "evaluation",
        CategoryEvaluation,
        {
            "category": "technical_knowledge",
            "score": None,
            "sufficient_evidence": False,
            "reasoning": "Insufficient evidence",
            "evidence": [],
            "areas_to_validate": ["Python depth"],
        },
    ),
]


def _request(task: str = "job_analysis") -> LLMRequest:
    return LLMRequest(
        task=task,
        system="System instructions",
        user="User content",
        context={},
        max_tokens=321,
        temperature=0.0,
    )


def _service(chat: FakeChatAPI) -> MistralLLMService:
    return MistralLLMService(
        api_key="unit-test-key",
        client=FakeMistralClient(chat),
        api_error_types=(FakeMistralAPIError,),
    )


@pytest.mark.parametrize(("task", "schema", "payload"), TASK_CASES)
def test_every_supported_task_uses_pydantic_structured_output(
    task: str,
    schema: type[BaseModel],
    payload: dict[str, Any],
) -> None:
    chat = FakeChatAPI([payload])
    service = _service(chat)

    result = service.generate_structured(_request(task), schema)

    assert isinstance(result, schema)
    assert len(chat.parse_calls) == 1
    call = chat.parse_calls[0]
    assert call["response_format"] is schema
    assert call["model"] == DEFAULT_MISTRAL_MODEL
    assert call["max_tokens"] == 321
    assert call["temperature"] == 0.0
    assert call["messages"] == [
        {"role": "system", "content": "System instructions"},
        {"role": "user", "content": "User content"},
    ]


def test_structured_output_retries_once_after_pydantic_validation_error() -> None:
    chat = FakeChatAPI([
        {"name": "first response is missing score"},
        {"name": "repaired", "score": 91},
    ])
    service = _service(chat)

    result = service.generate_structured(_request(), SampleOutput)

    assert result == SampleOutput(name="repaired", score=91)
    assert len(chat.parse_calls) == 2
    repair_prompt = chat.parse_calls[1]["messages"][-1]["content"]
    assert "failed schema validation" in repair_prompt
    assert "content concise" in repair_prompt
    assert "JSON finishes within the response limit" in repair_prompt
    assert "score" in repair_prompt
    assert chat.parse_calls[0]["max_tokens"] == 321
    assert chat.parse_calls[1]["max_tokens"] == 642


def test_second_structured_validation_failure_raises_safe_error() -> None:
    invalid = {"name": "response contains private candidate material"}
    chat = FakeChatAPI([invalid, invalid])
    service = _service(chat)

    with pytest.raises(LLMStructuredError) as exc_info:
        service.generate_structured(_request(), SampleOutput)

    assert len(chat.parse_calls) == 2
    assert "after one repair attempt" in str(exc_info.value)
    assert "private candidate material" not in str(exc_info.value)


def test_api_errors_are_wrapped_without_exposing_provider_details() -> None:
    chat = FakeChatAPI([
        FakeMistralAPIError("request failed with unit-test-key and raw candidate text")
    ])
    service = _service(chat)

    with pytest.raises(LLMProviderError) as exc_info:
        service.generate_structured(_request(), SampleOutput)

    message = str(exc_info.value)
    assert message == "Mistral API request failed (HTTP 429)."
    assert "unit-test-key" not in message
    assert "candidate" not in message


def test_generate_text_uses_same_model_and_never_sends_api_key_in_payload() -> None:
    chat = FakeChatAPI(text_outcome="Mistral text response")
    service = _service(chat)

    result = service.generate_text(_request())

    assert result == "Mistral text response"
    assert chat.complete_calls[0]["model"] == DEFAULT_MISTRAL_MODEL
    serialized_call = json.dumps(chat.complete_calls[0])
    assert "unit-test-key" not in serialized_call


def test_service_rejects_blank_api_key() -> None:
    with pytest.raises(LLMConfigurationError, match="must not be blank"):
        MistralLLMService(api_key="   ", client=FakeMistralClient(FakeChatAPI()))


def test_service_rejects_unsupported_task_before_provider_call() -> None:
    chat = FakeChatAPI([])
    service = _service(chat)
    with pytest.raises(UnsupportedTaskError):
        service.generate_structured(_request("unsupported"), SampleOutput)
    assert chat.parse_calls == []
