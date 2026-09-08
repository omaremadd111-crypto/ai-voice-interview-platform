"""Voice agent persona: how the AI interviewer sounds and behaves.

This is presentation of the conversation ONLY. Nothing here reaches the
evaluator, the overall score, or the screening outcome -- those stay
deterministic and are computed in Python from config/rubrics.json. Changing an
agent's tone must never change a candidate's result.

Two rules are enforced structurally rather than left to documentation:
  - The opening must disclose that the interviewer is an AI (SPEC 13: never
    pretend to be human).
  - No field may configure emotion, accent, personality, or biometric analysis;
    those are permanently prohibited.
"""
import re

from pydantic import BaseModel, Field, field_validator, model_validator

# Substrings that would indicate someone is trying to configure a prohibited
# inference. Matched against every free-text field.
PROHIBITED_BEHAVIOUR_MARKERS: tuple[str, ...] = (
    "emotion detection",
    "emotion analysis",
    "detect emotion",
    "analyze emotion",
    "analyse emotion",
    "sentiment scoring",
    "accent analysis",
    "detect accent",
    "accent scoring",
    "personality analysis",
    "personality inference",
    "personality test",
    "personality scoring",
    "biometric",
    "voiceprint",
    "voice print",
    "facial analysis",
    "face analysis",
)

# The opening must make the AI nature explicit. Any one of these is enough.
_AI_DISCLOSURE_MARKERS: tuple[str, ...] = (
    "ai ",
    "ai.",
    "ai,",
    "ai'",
    "a.i.",
    "artificial intelligence",
    "automated",
    "virtual assistant",
    "bot",
)


class ConversationalStyle(BaseModel):
    """Delivery behaviours for the future voice transport.

    These are switches for how the agent converses; the interview flow itself
    (question order, follow-up limits, scoring) stays owned by InterviewEngine.
    """

    use_candidate_name: bool = True
    brief_acknowledgements: bool = True
    allow_question_rephrasing: bool = True
    natural_pauses: bool = True
    allow_interruptions: bool = True

    model_config = {"extra": "forbid"}


class VoiceAgentPersona(BaseModel):
    agent_name: str = Field(min_length=1, max_length=80)
    company_name: str = Field(min_length=1, max_length=120)
    ai_role_title: str = Field(
        default="AI screening assistant",
        min_length=1,
        max_length=120,
        description="How the agent describes its own role. Must read as an AI role.",
    )
    language: str = Field(default="English", min_length=1, max_length=60)
    tone: str = Field(default="professional and warm", min_length=1, max_length=200)

    opening_script: str = Field(min_length=1, max_length=1000)
    closing_script: str = Field(min_length=1, max_length=1000)

    off_topic_redirection: str = Field(
        default=(
            "Acknowledge the point briefly, then guide the conversation back to the "
            "current question without dismissing the candidate."
        ),
        min_length=1,
        max_length=600,
    )
    follow_up_style: str = Field(
        default=(
            "Ask at most one focused follow-up when an answer is promising but thin, "
            "and move on once the point is covered."
        ),
        min_length=1,
        max_length=600,
    )
    candidate_question_handling: str = Field(
        default=(
            "Answer factual questions about the role and process. For anything about "
            "outcome, salary, or a hiring decision, explain that a human recruiter "
            "will follow up."
        ),
        min_length=1,
        max_length=600,
    )
    conversational_style: ConversationalStyle = Field(default_factory=ConversationalStyle)

    model_config = {"extra": "forbid"}

    @field_validator(
        "agent_name",
        "company_name",
        "ai_role_title",
        "language",
        "tone",
        "opening_script",
        "closing_script",
        "off_topic_redirection",
        "follow_up_style",
        "candidate_question_handling",
    )
    @classmethod
    def _no_prohibited_behaviour(cls, value: str) -> str:
        lowered = value.casefold()
        for marker in PROHIBITED_BEHAVIOUR_MARKERS:
            if marker in lowered:
                raise ValueError(
                    f"'{marker}' is not configurable: emotion, accent, personality and "
                    f"biometric inference are permanently prohibited."
                )
        return value.strip()

    @model_validator(mode="after")
    def _opening_discloses_ai(self) -> "VoiceAgentPersona":
        lowered = f" {self.opening_script.casefold()} "
        # Word-boundary check for bare "ai" so "said", "again" etc. never count.
        if re.search(r"\ba\.?i\.?\b", lowered) or any(
            marker in lowered for marker in _AI_DISCLOSURE_MARKERS
        ):
            return self
        raise ValueError(
            "The opening script must disclose that the interviewer is an AI "
            "(for example: \"I'm Aimy, an AI screening assistant\"). The agent must "
            "never present itself as a human."
        )


def default_persona(agent_name: str = "Aimy", company_name: str = "Your Company") -> VoiceAgentPersona:
    """A ready-to-edit persona that already satisfies the disclosure rule."""
    return VoiceAgentPersona(
        agent_name=agent_name,
        company_name=company_name,
        opening_script=(
            f"Hi {{candidate_first_name}}, I'm {agent_name}, {company_name}'s AI screening "
            f"assistant. I'll be conducting your initial interview today. It should take "
            f"about 20 minutes, and a human recruiter reviews everything afterwards."
        ),
        closing_script=(
            "That's everything from me — thank you for your time, "
            "{candidate_first_name}. A member of the team will be in touch about next steps."
        ),
    )
