"""Unit tests for agents/voice_intent_classifier.py.

Uses a fake LLMService so these tests are fast, offline, and deterministic --
no real Mistral calls. Real-provider latency is measured separately and only informs the
Settings default, not what these tests assert.
"""
import time

import pytest

from agents.voice_intent_classifier import VoiceIntentClassificationError, VoiceIntentClassifier
from models.voice_intent import VoiceIntent, VoiceIntentResult
from services.llm.base import LLMProviderError, LLMRequest, LLMStructuredError


class FakeLLMService:
    provider_name = "fake"
    is_mock = False

    def __init__(self, *, result=None, error=None, delay_seconds: float = 0.0):
        self._result = result
        self._error = error
        self._delay_seconds = delay_seconds
        self.requests: list[LLMRequest] = []

    def generate_structured(self, request, schema):
        self.requests.append(request)
        if self._delay_seconds:
            time.sleep(self._delay_seconds)
        if self._error is not None:
            raise self._error
        assert schema is VoiceIntentResult
        return self._result

    def generate_text(self, request):
        raise NotImplementedError


def test_classify_returns_the_llm_services_result() -> None:
    expected = VoiceIntentResult(intent=VoiceIntent.DONT_KNOW, confidence=0.95)
    llm = FakeLLMService(result=expected)
    classifier = VoiceIntentClassifier(llm, timeout_seconds=2.0)

    result = classifier.classify("Tell me about Kubernetes.", "I'm not familiar with that.")

    assert result == expected
    assert len(llm.requests) == 1
    assert llm.requests[0].task == "voice_intent"
    assert "Kubernetes" in llm.requests[0].user
    assert "not familiar with that" in llm.requests[0].user


def test_classify_wraps_llm_errors() -> None:
    llm = FakeLLMService(error=LLMStructuredError("bad schema"))
    classifier = VoiceIntentClassifier(llm, timeout_seconds=2.0)

    with pytest.raises(VoiceIntentClassificationError):
        classifier.classify("question", "utterance")


def test_classify_wraps_provider_errors() -> None:
    llm = FakeLLMService(error=LLMProviderError("network down"))
    classifier = VoiceIntentClassifier(llm, timeout_seconds=2.0)

    with pytest.raises(VoiceIntentClassificationError):
        classifier.classify("question", "utterance")


def test_classify_raises_on_timeout_without_waiting_for_the_slow_call() -> None:
    llm = FakeLLMService(
        result=VoiceIntentResult(intent=VoiceIntent.SUBSTANTIVE_ANSWER, confidence=0.9),
        delay_seconds=2.0,
    )
    classifier = VoiceIntentClassifier(llm, timeout_seconds=0.1)

    started = time.monotonic()
    with pytest.raises(VoiceIntentClassificationError):
        classifier.classify("question", "utterance")
    elapsed = time.monotonic() - started

    # The whole point of the timeout: the caller must not be blocked anywhere
    # near the slow call's real duration (2s) -- a generous margin above the
    # 0.1s timeout still proves the ThreadPoolExecutor.__exit__(wait=True) trap
    # documented in the implementation is not silently reintroduced.
    assert elapsed < 1.0


def test_timeout_seconds_must_be_positive() -> None:
    with pytest.raises(ValueError):
        VoiceIntentClassifier(FakeLLMService(), timeout_seconds=0)
