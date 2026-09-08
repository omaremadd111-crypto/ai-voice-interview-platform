"""Semantic classification of a single candidate voice utterance.

This is the taxonomy foundation for the full conversational-quality plan, but
only a narrow slice of it changes behaviour in Phase 1: DONT_KNOW and
GENUINE_REFUSAL are the only intents that suppress a follow-up, and that
suppression still happens exclusively inside InterviewEngine (see
services/interview_engine.py). The classifier that produces a VoiceIntentResult
never touches session state -- it proposes a label, exactly like
agents/interviewer.py already proposes a follow-up decision.
"""
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class VoiceIntent(StrEnum):
    """Full taxonomy. Phase 1 only special-cases DONT_KNOW/GENUINE_REFUSAL;
    every other value is routed as ordinary answer content until a later phase
    wires its own dispatch (rephrase/repeat/off-topic/candidate-question/etc.
    already have deterministic handling upstream of the classifier)."""

    SUBSTANTIVE_ANSWER = "substantive_answer"
    PARTIAL_ANSWER = "partial_answer"
    HESITATION = "hesitation"
    DONT_KNOW = "dont_know"
    GENUINE_REFUSAL = "genuine_refusal"
    REPHRASE_REQUEST = "rephrase_request"
    REPEAT_REQUEST = "repeat_request"
    CANDIDATE_QUESTION = "candidate_question"
    OFF_TOPIC = "off_topic"
    STOP_REQUEST = "stop_request"
    CONNECTION_CHECK = "connection_check"
    INCOMPLETE_UTTERANCE = "incomplete_utterance"


#: The only intents that change InterviewEngine follow-up behaviour in Phase 1.
NO_FOLLOW_UP_INTENTS = frozenset({VoiceIntent.DONT_KNOW, VoiceIntent.GENUINE_REFUSAL})

_MAX_REACTION_LENGTH = 160
_MAX_RESOLVED_TEXT_LENGTH = 2000
_MAX_TRANSITION_LENGTH = 160


class VoiceIntentResult(BaseModel):
    """One structured classification call's output.

    Deliberately a single schema for the whole decision so the delivery layer
    never needs a second LLM round trip to react naturally -- `reaction` and
    `transition` ride along with the classification itself (Phase 3). A separate
    follow-up LLM call (agents/interviewer.py) still only happens when
    InterviewEngine's own caps say a follow-up is eligible; this schema has no
    opinion on that.
    """

    intent: VoiceIntent
    confidence: float = Field(ge=0.0, le=1.0)
    #: A short, natural, content-caused spoken reaction ("Got it.",
    #: "No problem, let's move on."). Never used to change what gets recorded as
    #: the candidate's answer, and filtered for banned vocabulary before being
    #: spoken (Phase 3 safety constraint).
    reaction: str | None = Field(default=None, max_length=_MAX_REACTION_LENGTH)
    #: Reserved (hesitant-fragment cleanup). The literal candidate utterance is
    #: ALWAYS what gets submitted; this field is ignored by dispatch.
    resolved_text: str | None = Field(default=None, max_length=_MAX_RESOLVED_TEXT_LENGTH)
    #: A short context-sensitive segue ("Let's switch to troubleshooting.")
    #: produced when the caller indicates the next question moves to a new
    #: category. The dispatcher speaks it only if the engine actually advances
    #: to that different category; otherwise it is dropped.
    transition: str | None = Field(default=None, max_length=_MAX_TRANSITION_LENGTH)

    @field_validator("reaction", "resolved_text", "transition")
    @classmethod
    def _blank_becomes_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None
