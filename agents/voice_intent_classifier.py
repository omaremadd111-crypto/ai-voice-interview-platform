"""Voice intent classifier: proposes what a candidate's utterance means.

Proposes only -- exactly like agents/interviewer.py proposes a follow-up
decision. This class never touches InterviewSession, never decides whether an
interview continues, and never scores anything. The caller (
application/voice_conversation_service.py) is the only place a
VoiceIntentResult gets turned into an action, and even there the action is
limited to an optional intent_hint passed into InterviewEngine.submit_answer,
which enforces its own deterministic suppression rule regardless of what the
classifier says (see services/interview_engine.py: NO_FOLLOW_UP_INTENTS is
only ever consulted alongside the pre-existing regex gate, never instead of
engine-owned caps and counters).

Depends only on LLMService -- no provider SDK, no LiveKit, no Gradio.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from models.voice_intent import VoiceIntentResult
from prompts.voice_intent import SYSTEM_PROMPT, build_user_prompt
from services.llm.base import LLMError, LLMRequest, LLMService

#: Structured classification output is small and bounded; this is a ceiling; the
#: real per-call budget lives in Settings.voice_intent_classifier_timeout_seconds.
_MAX_OUTPUT_TOKENS = 200


class VoiceIntentClassificationError(Exception):
    """Raised when a classification could not be produced in time or at all.

    Callers must treat this exactly like "no classifier available" and fall
    back to the pre-existing deterministic behaviour -- never propagate it into
    the live conversation as an error the candidate hears.
    """


class VoiceIntentClassifier:
    def __init__(self, llm_service: LLMService, *, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._llm = llm_service
        self._timeout_seconds = timeout_seconds

    def classify(
        self,
        question_text: str,
        utterance: str,
        *,
        upcoming_category: str | None = None,
    ) -> VoiceIntentResult:
        """Classify one candidate utterance, bounded by timeout_seconds.

        Takes the spoken question text only -- not the full InterviewQuestion or
        transcript history. A single utterance's dont_know/genuine_refusal-vs-
        answer distinction does not need cross-turn context. ``upcoming_category``
        (Phase 3) is the engine-owned category of the NEXT plan question, or None
        when unknown; it only shapes the optional `transition` field so the same
        single call can carry a context-sensitive segue -- never the intent
        itself.

        A blocking-call timeout is enforced with a worker thread rather than
        asyncio cancellation: VoiceConversationService.respond() is a plain
        synchronous method (unchanged by this addition), and the underlying
        LLMService call is itself synchronous. The worker thread is not force-
        killed on timeout -- the HTTP request may complete in the background --
        but the caller never waits past timeout_seconds for a result.
        """
        request = LLMRequest(
            task="voice_intent",
            system=SYSTEM_PROMPT,
            user=build_user_prompt(question_text, utterance, upcoming_category=upcoming_category),
            context={
                "question": {"expected_topics": []},
                "latest_answer": utterance,
                **({"upcoming_category": upcoming_category} if upcoming_category else {}),
            },
            temperature=0.0,
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
        # Not a context manager on purpose: ThreadPoolExecutor.__exit__ calls
        # shutdown(wait=True), which would block the caller until the still-running
        # worker finishes even after future.result() has already timed out -- that
        # would silently turn every timeout back into a full-latency wait.
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(self._llm.generate_structured, request, VoiceIntentResult)
            try:
                return future.result(timeout=self._timeout_seconds)
            except FutureTimeoutError as exc:
                raise VoiceIntentClassificationError(
                    f"Voice intent classification exceeded {self._timeout_seconds}s."
                ) from exc
            except LLMError as exc:
                raise VoiceIntentClassificationError(
                    f"Voice intent classification failed: {type(exc).__name__}."
                ) from exc
        finally:
            pool.shutdown(wait=False)
