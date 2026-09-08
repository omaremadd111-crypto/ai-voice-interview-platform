"""OpenRouter provider contract tests with a deterministic OpenAI-SDK-shaped fake.

OpenRouter is OpenAI-compatible but most routed models (including the free
preview model this integration defaults to) do not natively guarantee
schema-constrained output, so structured output here goes through
services/llm/structured.py's schema-in-prompt + client-side validation instead
of a provider-native parse() call. These tests mirror
tests/test_mistral_service.py's coverage shape adapted for that mechanism.
"""
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from agents.interviewer import Interviewer
from agents.voice_intent_classifier import VoiceIntentClassifier
from models.candidate import CandidateAnalysis, FitAnalysis
from models.evaluation import CategoryEvaluation
from models.interview import FollowUpDecision, InterviewPlan
from models.job import JobAnalysis
from models.voice_intent import VoiceIntentResult
from services.llm.base import (
    LLMConfigurationError,
    LLMProviderError,
    LLMRequest,
    LLMStructuredError,
    UnsupportedTaskError,
)
from services.llm.openrouter_service import DEFAULT_OPENROUTER_MODEL, OpenRouterLLMService


class SampleOutput(BaseModel):
    name: str
    score: int


class FakeOpenRouterAPIError(Exception):
    status_code = 429


class FakeChatCompletionsAPI:
    def __init__(
        self,
        content_outcomes: list[Any] | None = None,
        text_outcome: str | Exception = "plain response",
    ) -> None:
        # Each structured outcome is either a dict (JSON-encoded before being
        # "returned" by the fake model) or an Exception raised as the API call.
        self.content_outcomes = list(content_outcomes or [])
        self.text_outcome = text_outcome
        self.create_calls: list[dict[str, Any]] = []
        self._mode = "structured"

    def create(self, **kwargs: Any) -> Any:
        self.create_calls.append(kwargs)
        if "response_format" in kwargs:
            outcome = self.content_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            content = json.dumps(outcome) if not isinstance(outcome, str) else outcome
        else:
            if isinstance(self.text_outcome, Exception):
                raise self.text_outcome
            content = self.text_outcome
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class FakeOpenRouterClient:
    def __init__(self, chat_completions: FakeChatCompletionsAPI) -> None:
        self.chat = SimpleNamespace(completions=chat_completions)


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
    (
        "voice_intent",
        VoiceIntentResult,
        {
            "intent": "dont_know",
            "confidence": 0.95,
            "reaction": "No problem, let's move on.",
            "resolved_text": None,
            "transition": None,
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


def _service(chat_completions: FakeChatCompletionsAPI) -> OpenRouterLLMService:
    return OpenRouterLLMService(
        api_key="unit-test-key",
        client=FakeOpenRouterClient(chat_completions),
        api_error_types=(FakeOpenRouterAPIError,),
    )


@pytest.mark.parametrize(("task", "schema", "payload"), TASK_CASES)
def test_every_supported_task_produces_valid_structured_output(
    task: str,
    schema: type[BaseModel],
    payload: dict[str, Any],
) -> None:
    chat = FakeChatCompletionsAPI([payload])
    service = _service(chat)

    result = service.generate_structured(_request(task), schema)

    assert isinstance(result, schema)
    assert len(chat.create_calls) == 1
    call = chat.create_calls[0]
    assert call["model"] == DEFAULT_OPENROUTER_MODEL
    assert call["max_tokens"] == 321
    assert call["temperature"] == 0.0
    assert call["response_format"] == {"type": "json_object"}
    # Reasoning disabled on every request: real-workload benchmarking showed
    # reasoning models routed through OpenRouter spend the entire completion
    # budget on a hidden reasoning field before ever writing content, reliably
    # producing empty output at this project's real (small) token budgets.
    assert call["extra_body"] == {"reasoning": {"enabled": False}}
    assert call["messages"][1] == {"role": "user", "content": "User content"}
    # Schema instructions are appended to the system prompt (services/llm/
    # structured.py) since this provider cannot rely on native schema
    # enforcement the way Mistral's official client does.
    system_content = call["messages"][0]["content"]
    assert system_content.startswith("System instructions")
    assert "JSON Schema" in system_content


def test_configured_model_is_used_when_provided() -> None:
    chat = FakeChatCompletionsAPI([{"name": "x", "score": 1}])
    service = OpenRouterLLMService(
        api_key="unit-test-key",
        model="some-other/model:free",
        client=FakeOpenRouterClient(chat),
        api_error_types=(FakeOpenRouterAPIError,),
    )

    service.generate_structured(_request(), SampleOutput)

    assert chat.create_calls[0]["model"] == "some-other/model:free"


def test_structured_output_retries_once_after_validation_error() -> None:
    chat = FakeChatCompletionsAPI([
        {"name": "first response is missing score"},
        {"name": "repaired", "score": 91},
    ])
    service = _service(chat)

    result = service.generate_structured(_request(), SampleOutput)

    assert result == SampleOutput(name="repaired", score=91)
    assert len(chat.create_calls) == 2
    repair_prompt = chat.create_calls[1]["messages"][-1]["content"]
    assert "failed schema validation" in repair_prompt
    assert "content concise" in repair_prompt
    assert "JSON finishes within the response limit" in repair_prompt
    assert chat.create_calls[0]["max_tokens"] == 321
    assert chat.create_calls[1]["max_tokens"] == 642


def test_second_structured_validation_failure_raises_safe_error() -> None:
    invalid = {"name": "response contains private candidate material"}
    chat = FakeChatCompletionsAPI([invalid, invalid])
    service = _service(chat)

    with pytest.raises(LLMStructuredError) as exc_info:
        service.generate_structured(_request(), SampleOutput)

    assert len(chat.create_calls) == 2
    assert "after one repair attempt" in str(exc_info.value)
    assert "private candidate material" not in str(exc_info.value)


def test_malformed_json_response_is_treated_as_a_validation_failure() -> None:
    """A model that ignores the JSON-mode hint and returns prose must be
    recovered by the repair loop, not crash with a raw JSONDecodeError."""
    chat = FakeChatCompletionsAPI([
        "Sure! Here's my answer: not actually JSON at all.",
        {"name": "repaired", "score": 5},
    ])
    service = _service(chat)

    result = service.generate_structured(_request(), SampleOutput)

    assert result == SampleOutput(name="repaired", score=5)
    assert len(chat.create_calls) == 2


def test_api_errors_are_wrapped_without_exposing_provider_details() -> None:
    chat = FakeChatCompletionsAPI([
        FakeOpenRouterAPIError("request failed with unit-test-key and raw candidate text")
    ])
    service = _service(chat)

    with pytest.raises(LLMProviderError) as exc_info:
        service.generate_structured(_request(), SampleOutput)

    message = str(exc_info.value)
    assert message == "OpenRouter API request failed (HTTP 429)."
    assert "unit-test-key" not in message
    assert "candidate" not in message


def test_generate_text_uses_same_model_and_never_sends_api_key_in_payload() -> None:
    chat = FakeChatCompletionsAPI(text_outcome="OpenRouter text response")
    service = _service(chat)

    result = service.generate_text(_request())

    assert result == "OpenRouter text response"
    assert chat.create_calls[0]["model"] == DEFAULT_OPENROUTER_MODEL
    assert "response_format" not in chat.create_calls[0]
    assert chat.create_calls[0]["extra_body"] == {"reasoning": {"enabled": False}}
    serialized_call = json.dumps(chat.create_calls[0])
    assert "unit-test-key" not in serialized_call


def test_generate_text_error_is_wrapped() -> None:
    chat = FakeChatCompletionsAPI(text_outcome=FakeOpenRouterAPIError("boom"))
    service = _service(chat)

    with pytest.raises(LLMProviderError):
        service.generate_text(_request())


def test_provider_module_never_logs_anything() -> None:
    """The strongest guarantee against leaking the API key, Authorization
    header, or request/response content: this module makes zero log_event
    calls (matches services/llm/mistral_service.py's identical discipline),
    so there is nothing for the logging layer's redaction to ever need to
    catch here in the first place."""
    import inspect

    import services.llm.openrouter_service as module

    source = inspect.getsource(module)
    assert "log_event" not in source
    assert "logging.getLogger" not in source


def test_service_rejects_blank_api_key() -> None:
    with pytest.raises(LLMConfigurationError, match="must not be blank"):
        OpenRouterLLMService(
            api_key="   ", client=FakeOpenRouterClient(FakeChatCompletionsAPI()),
        )


def test_service_defaults_to_the_benchmarked_live_model() -> None:
    service = OpenRouterLLMService(
        api_key="unit-test-key", client=FakeOpenRouterClient(FakeChatCompletionsAPI()),
    )
    assert service.model == "nvidia/nemotron-3.5-lightning:free"
    assert service.model == DEFAULT_OPENROUTER_MODEL


def test_service_rejects_unsupported_task_before_provider_call() -> None:
    chat = FakeChatCompletionsAPI([])
    service = _service(chat)
    with pytest.raises(UnsupportedTaskError):
        service.generate_structured(_request("unsupported"), SampleOutput)
    assert chat.create_calls == []


def test_missing_sdk_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors Mistral's behaviour: a real-provider service without an
    injected client tries to import the SDK lazily, and a missing SDK must
    fail with a clear, actionable LLMConfigurationError."""
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openai":
            raise ImportError("simulated missing openai package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    service = OpenRouterLLMService(api_key="unit-test-key")

    with pytest.raises(LLMConfigurationError, match="OpenAI SDK is not installed"):
        service.generate_text(_request())


# ---- Direct integration with the existing structured-output consumers -----
#
# VoiceIntentClassifier and Interviewer depend only on the LLMService
# interface (LLMRequest in, validated Pydantic model out) -- these tests wire
# them directly to an OpenRouterLLMService backed by a fake client, proving
# the new provider is a drop-in for both without any change to either class.


def test_voice_intent_classifier_works_through_openrouter() -> None:
    chat = FakeChatCompletionsAPI([{
        "intent": "dont_know",
        "confidence": 0.95,
        "reaction": "No problem, let's move on.",
        "resolved_text": None,
        "transition": None,
    }])
    service = _service(chat)
    classifier = VoiceIntentClassifier(service, timeout_seconds=5.0)

    result = classifier.classify(
        "Tell me about your Kubernetes experience.", "I'm not familiar with that.",
    )

    assert result.intent.value == "dont_know"
    assert result.confidence == 0.95
    assert chat.create_calls[0]["response_format"] == {"type": "json_object"}


def test_interviewer_follow_up_generation_works_through_openrouter() -> None:
    from models.common import QuestionCategory
    from models.interview import InterviewQuestion

    chat = FakeChatCompletionsAPI([{
        "should_ask": True,
        "follow_up_question": "How did you test it?",
        "reason": "Testing evidence is unclear.",
    }])
    service = _service(chat)
    interviewer = Interviewer(service)
    question = InterviewQuestion(
        id="q1",
        category=QuestionCategory.TECHNICAL,
        question="Explain your Python API work.",
        purpose="Validate relevant technical experience.",
        expected_topics=["Python"],
        difficulty="junior",
    )

    decision = interviewer.propose_follow_up(question, [], "I built a Python API.")

    assert isinstance(decision, FollowUpDecision)
    assert decision.should_ask is True
    assert decision.follow_up_question == "How did you test it?"
