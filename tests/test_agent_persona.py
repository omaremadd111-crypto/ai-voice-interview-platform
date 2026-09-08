"""Voice agent persona validation. No database required.

Two rules are structural, not documentation: the opening must disclose the AI,
and no field may configure a prohibited inference.
"""
import pytest
from pydantic import ValidationError

from models.agent_persona import VoiceAgentPersona, default_persona


def _persona(**overrides: object) -> VoiceAgentPersona:
    base = {
        "agent_name": "Aimy",
        "company_name": "FlairsTech",
        "opening_script": "Hi Hadeer, I'm Aimy, FlairsTech's AI screening assistant.",
        "closing_script": "Thanks for your time. A recruiter will follow up.",
    }
    base.update(overrides)
    return VoiceAgentPersona.model_validate(base)


def test_default_persona_is_valid_and_discloses_ai() -> None:
    persona = default_persona(agent_name="Aimy", company_name="FlairsTech")
    assert persona.agent_name == "Aimy"
    assert "AI" in persona.opening_script
    assert persona.conversational_style.use_candidate_name is True


def test_all_configurable_fields_round_trip() -> None:
    persona = _persona(
        ai_role_title="AI recruiting assistant",
        language="English",
        tone="warm and concise",
        off_topic_redirection="Acknowledge briefly, then return to the question.",
        follow_up_style="One focused follow-up when an answer is thin.",
        candidate_question_handling="Answer role questions; defer decisions to a recruiter.",
    )
    dumped = persona.model_dump(mode="json")
    for field in (
        "agent_name", "company_name", "ai_role_title", "language", "tone",
        "opening_script", "closing_script", "off_topic_redirection",
        "follow_up_style", "candidate_question_handling", "conversational_style",
    ):
        assert field in dumped


def test_conversational_style_flags_are_configurable() -> None:
    persona = _persona(conversational_style={
        "use_candidate_name": True,
        "brief_acknowledgements": True,
        "allow_question_rephrasing": False,
        "natural_pauses": True,
        "allow_interruptions": True,
    })
    assert persona.conversational_style.allow_question_rephrasing is False
    assert persona.conversational_style.allow_interruptions is True


@pytest.mark.parametrize(
    "opening",
    [
        "Hi Hadeer, I'm Aimy from FlairsTech. Let's begin.",
        "Good morning, this is Aimy calling from the recruitment team.",
        "Hello! Thanks for making time today.",
    ],
)
def test_opening_without_ai_disclosure_is_rejected(opening: str) -> None:
    """The agent must never be able to present itself as a human."""
    with pytest.raises(ValidationError) as exc:
        _persona(opening_script=opening)
    assert "disclose" in str(exc.value).lower()


@pytest.mark.parametrize(
    "opening",
    [
        "Hi Hadeer, I'm Aimy, FlairsTech's AI screening assistant.",
        "Hello, I am an A.I. interviewer for FlairsTech.",
        "Hi, this is an automated screening interview.",
        "I'm a virtual assistant conducting your first interview.",
    ],
)
def test_openings_that_disclose_ai_are_accepted(opening: str) -> None:
    assert _persona(opening_script=opening).opening_script == opening


def test_word_boundary_prevents_false_positive_ai_disclosure() -> None:
    """"again" and "said" contain the letters a-i but disclose nothing."""
    with pytest.raises(ValidationError):
        _persona(opening_script="Hello again, thanks for coming; as I said, let's start.")


@pytest.mark.parametrize(
    "prohibited",
    [
        "Use emotion detection to gauge confidence.",
        "Perform accent analysis on the candidate.",
        "Run a personality analysis during the call.",
        "Capture a voiceprint for biometric matching.",
        "Do facial analysis of the candidate on video.",
    ],
)
def test_prohibited_inference_settings_are_rejected(prohibited: str) -> None:
    with pytest.raises(ValidationError) as exc:
        _persona(tone=prohibited)
    assert "prohibited" in str(exc.value).lower()


def test_prohibited_settings_rejected_in_every_free_text_field() -> None:
    for field in ("off_topic_redirection", "follow_up_style", "candidate_question_handling"):
        with pytest.raises(ValidationError):
            _persona(**{field: "Apply emotion analysis to the answer."})


def test_unknown_fields_are_rejected() -> None:
    """extra=forbid keeps a prohibited capability from riding along under a name
    the validator does not screen."""
    with pytest.raises(ValidationError):
        _persona(emotion_scoring_enabled=True)


def test_persona_carries_no_scoring_or_evaluation_fields() -> None:
    """Tone must never be able to influence a candidate's result."""
    fields = set(VoiceAgentPersona.model_fields)
    assert not (
        fields
        & {"score", "weights", "rubric_profile", "pass_score_threshold", "screening_outcome"}
    )
