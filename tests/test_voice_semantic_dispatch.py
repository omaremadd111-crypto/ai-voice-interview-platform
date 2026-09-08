"""Phase 3: full semantic conversation dispatch.

Covers the plan section 24 Phase-3 gate: semantic recognition of repeat_request,
rephrase_request, candidate_question, off_topic, and hesitation across natural
paraphrases; meta-intents produce NO transcript turn; rephrasing preserves the
approved question's substance (I1); reactions are content-caused with a
safety filter on all generated spoken text; candidate-question ANSWERS stay
deterministic; off-topic bias toward answers; exactly ONE classification call
per ambiguous turn.
"""
from types import SimpleNamespace

import pytest

from models.agent_persona import default_persona
from models.common import InterviewState, QuestionCategory
from models.interview import NextPrompt
from models.platform import AgentConfigRecord, CandidateRecord, Position
from models.voice_intent import VoiceIntent, VoiceIntentResult
from services.llm.mock.mock_service import MockLLMService

from application.voice_conversation_service import VoiceConversationService


class FakeAgentService:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.submitted_hints: list[VoiceIntent | None] = []
        self.ended = False
        self.current_calls = 0
        self.upcoming_category: QuestionCategory | None = QuestionCategory.PROBLEM_SOLVING
        self.next_prompt = NextPrompt(
            state=InterviewState.IN_PROGRESS,
            question_id="q1",
            question_text="Tell me about a project you led?",
            category=QuestionCategory.CV_PROJECT_VALIDATION,
            total_questions=3,
        )
        self.queue: list[NextPrompt] = []

    def start_interview(self, session_id: str) -> NextPrompt:
        return self.next_prompt

    def submit_answer(self, session_id: str, answer: str, *, intent_hint=None) -> NextPrompt:
        self.submitted.append(answer)
        self.submitted_hints.append(intent_hint)
        if self.queue:
            self.next_prompt = self.queue.pop(0)
        return self.next_prompt

    def get_current_prompt(self, session_id: str) -> NextPrompt:
        self.current_calls += 1
        return self.next_prompt

    def get_upcoming_category(self, session_id: str) -> QuestionCategory | None:
        return self.upcoming_category

    def end_interview(self, session_id: str) -> object:
        self.ended = True
        return object()


class FakeClassifier:
    """Utterance-keyed canned results; counts calls for the single-call rule."""

    def __init__(self, results: dict[str, VoiceIntentResult]) -> None:
        self.results = results
        self.call_count = 0

    def classify(
        self, question_text: str, utterance: str, *, upcoming_category: str | None = None,
    ) -> VoiceIntentResult:
        self.call_count += 1
        result = self.results.get(utterance)
        if result is not None:
            return result
        return VoiceIntentResult(intent=VoiceIntent.SUBSTANTIVE_ANSWER, confidence=0.9)


def build_service(persona=None, classifier=None):
    agent = FakeAgentService()
    candidate = CandidateRecord(id=4, position_id=3, full_name="Sam Taylor")
    position = Position(id=3, owner_id=2, company_name="Acme", title="Engineer")
    configs = []
    if persona is not None:
        configs.append(AgentConfigRecord(
            id=1, owner_id=2, position_id=3, name="Aimy",
            config=persona.model_dump(mode="json"),
        ))
    service = VoiceConversationService(
        agent,  # type: ignore[arg-type]
        SimpleNamespace(get=lambda _: candidate),
        SimpleNamespace(get=lambda _: position),
        SimpleNamespace(list=lambda *, owner_id, position_id=None: configs),  # type: ignore[arg-type]
        classifier,
    )
    context = service.load_context(session_id="s1", candidate_id=4, position_id=3)
    return service, agent, context


# ---- semantic meta-intent paraphrases (>=4 each, plan section 24) -------------


REPEAT_PARAPHRASES = (
    "Could you say that again?",
    "Sorry, come again?",
    "I didn't catch the question.",
    "What was that one more time?",
    "Can you repeat what you asked?",
)
REPHRASE_PARAPHRASES = (
    "What do you mean by that?",
    "Could you rephrase the question?",
    "I don't understand what you're asking.",
    "Can you put it differently?",
    "That doesn't make sense to me, can you clarify?",
)
CANDIDATE_QUESTION_PARAPHRASES = (
    "So what's the deal with remote work here?",
    "What happens after this interview?",
    "Where would I actually be working?",
    "Do employees get benefits?",
    "When would the start date be?",
)
OFF_TOPIC_PARAPHRASES = (
    "Tell me a joke while we're here.",
    "How about that weather we've been having?",
    "Did you watch the football score last night?",
    "What's your favorite movie?",
)
HESITATION_PARAPHRASES = (
    "Hold on a sec.",
    "Bear with me for a moment.",
    "Let me gather my thoughts.",
    "Just a second please.",
)


@pytest.mark.parametrize("utterance", REPEAT_PARAPHRASES)
def test_repeat_requests_are_recognised_semantically_and_never_submitted(utterance: str) -> None:
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.REPEAT_REQUEST, confidence=0.92),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, utterance)

    assert agent.submitted == []
    assert agent.submitted_hints == []
    # The approved question's own text comes back verbatim.
    assert reply.segments[-1] == agent.next_prompt.question_text


@pytest.mark.parametrize("utterance", REPHRASE_PARAPHRASES)
def test_rephrase_requests_are_recognised_and_preserve_the_question_substance(
    utterance: str,
) -> None:
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.REPHRASE_REQUEST, confidence=0.9),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, utterance)

    assert agent.submitted == []
    rephrased = reply.segments[-1]
    assert agent.next_prompt.question_text != rephrased or "Sure" in rephrased
    # Substance preserved (I1): the project topic from the approved plan remains.
    assert "project" in rephrased.lower()


@pytest.mark.parametrize("utterance", CANDIDATE_QUESTION_PARAPHRASES)
def test_candidate_questions_are_recognised_semantically_without_transcript_turns(
    utterance: str,
) -> None:
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.CANDIDATE_QUESTION, confidence=0.88),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, utterance)

    assert agent.submitted == []
    assert reply.segments[-1] == agent.next_prompt.question_text


@pytest.mark.parametrize("utterance", OFF_TOPIC_PARAPHRASES)
def test_off_topic_drift_is_redirected_without_being_stored(utterance: str) -> None:
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.OFF_TOPIC, confidence=0.87),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, utterance)

    assert agent.submitted == []
    assert reply.segments[-1] == agent.next_prompt.question_text


@pytest.mark.parametrize("utterance", HESITATION_PARAPHRASES)
def test_hesitation_gets_the_once_per_question_courtesy_only(utterance: str) -> None:
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.HESITATION, confidence=0.93),
        "Give me another moment please.": VoiceIntentResult(
            intent=VoiceIntent.HESITATION, confidence=0.93,
        ),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    first = service.respond(context, utterance)
    second = service.respond(context, "Give me another moment please.")

    assert agent.submitted == []
    assert first.segments == ("Take your time.",)
    assert second.segments == ()  # once per question


def test_connection_check_is_reassured_without_submission() -> None:
    utterance = "Can you hear me okay?"
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.CONNECTION_CHECK, confidence=0.97),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, utterance)

    assert agent.submitted == []
    assert "hear" in reply.segments[0].lower()


def test_semantic_stop_request_ends_the_interview_cleanly() -> None:
    utterance = "That's everything from my side."
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.STOP_REQUEST, confidence=0.9),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, utterance)

    assert agent.ended is True
    assert reply.finished is True
    assert agent.submitted == []


def test_semantic_incomplete_utterance_buffers_instead_of_submitting() -> None:
    utterance = "So basically after the migration we"
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.INCOMPLETE_UTTERANCE, confidence=0.85),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, utterance)

    assert agent.submitted == []
    assert reply.segments == ()
    # The words are buffered, not discarded: a continuation completes them.
    service.respond(context, "cut the deploy time in half.")
    assert agent.submitted == ["So basically after the migration we cut the deploy time in half."]


# ---- single-call rule ----------------------------------------------------------


def test_exactly_one_classification_call_per_ambiguous_turn() -> None:
    """Reaction + transition must ride on the SAME call -- never a second LLM
    round trip (plan sections 15/16)."""
    utterance = "We migrated billing onto Kubernetes and cut deploys from hours to minutes."
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(
            intent=VoiceIntent.SUBSTANTIVE_ANSWER,
            confidence=0.95,
            reaction="Got it.",
            transition=None,
        ),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    service.respond(context, utterance)

    assert classifier.call_count == 1


# ---- transitions ---------------------------------------------------------------


def test_category_change_adds_a_transition_segment() -> None:
    utterance = "The rollout covered every region by March."
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(
            intent=VoiceIntent.SUBSTANTIVE_ANSWER,
            confidence=0.95,
            reaction="Got it.",
            transition="Next, let's talk through some problem solving.",
        ),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)
    agent.queue.append(NextPrompt(
        state=InterviewState.IN_PROGRESS,
        question_id="q2",
        question_text="Describe a difficult production issue you solved.",
        category=QuestionCategory.PROBLEM_SOLVING,
        question_index=1,
        total_questions=3,
    ))

    reply = service.respond(context, utterance)

    assert reply.segments == (
        "Got it.",
        "Next, let's talk through some problem solving.",
        "Describe a difficult production issue you solved?",
    )


def test_no_transition_when_category_stays_the_same() -> None:
    utterance = "The rollout covered every region by March."
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(
            intent=VoiceIntent.SUBSTANTIVE_ANSWER,
            confidence=0.95,
            reaction="Got it.",
            transition="This segue must NOT be spoken.",
        ),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)
    # Same category follow-up prompt.
    agent.next_prompt = agent.next_prompt.model_copy(update={
        "question_text": "What did the rollout cost?",
        "is_follow_up": True,
    })

    reply = service.respond(context, utterance)

    assert not any("segue" in segment for segment in reply.segments)


def test_deterministic_fallback_transition_when_classifier_gives_none() -> None:
    utterance = "Everything shipped on schedule."
    classifier = FakeClassifier({utterance: VoiceIntentResult(
        intent=VoiceIntent.SUBSTANTIVE_ANSWER, confidence=0.9, reaction="Understood.",
    )})
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)
    agent.queue.append(NextPrompt(
        state=InterviewState.IN_PROGRESS,
        question_id="q2",
        question_text="Walk me through a time you disagreed with a teammate.",
        category=QuestionCategory.BEHAVIORAL,
        question_index=1,
        total_questions=3,
    ))

    reply = service.respond(context, utterance)

    assert any(
        "how you work with others" in segment for segment in reply.segments
    )


# ---- content-caused reactions --------------------------------------------------


def test_generated_reaction_is_used_over_rotation_when_cadence_allows() -> None:
    utterance = "I led the platform migration end to end."
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(
            intent=VoiceIntent.SUBSTANTIVE_ANSWER,
            confidence=0.94,
            reaction="A full platform migration — got it.",
        ),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, utterance)

    assert reply.segments[0] == "A full platform migration — got it."


def test_reaction_variety_no_immediate_mechanical_repetition() -> None:
    classifier = FakeClassifier({})
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    spoken_first_segments: list[str] = []
    for index in range(1, 6):
        utterance = f"Answer variant {index} with concrete metrics and outcomes."
        classifier.results[utterance] = VoiceIntentResult(
            intent=VoiceIntent.SUBSTANTIVE_ANSWER,
            confidence=0.9,
            reaction="Got it.",  # classifier keeps producing the same line...
        )
        reply = service.respond(context, utterance)
        if len(reply.segments) > 1:
            spoken_first_segments.append(reply.segments[0])

    # ...but the service never repeats the same generated line back-to-back:
    # repetition is the defect (plan section 15).
    for previous, current in zip(spoken_first_segments, spoken_first_segments[1:]):
        assert previous != current


# ---- SAFETY regression: banned vocabulary / performance commentary -------------


BANNED_VOCABULARY = (
    "hire", "hired", "hiring", "reject", "rejected", "rejection",
    "disqualified", "disqualify", "unqualified", "qualified",
    "competent", "incompetent", "impressive", "promising candidate",
    "strong candidate", "weak candidate", "good fit", "not a good fit",
    "score", "scoring", "evaluation", "evaluated", "assessment",
    "passed", "failed", "verdict", "shortlisted",
)
PROTECTED_ATTRIBUTE_TERMS = (
    "emotion", "emotional", "accent", "personality", "race", "racial",
    "gender", "religion", "nationality", "disability", "orientation",
    "appearance", "attractive", "sentiment",
)


@pytest.mark.parametrize("banned_term", BANNED_VOCABULARY)
def test_poisoned_reactions_containing_decision_or_performance_vocabulary_are_filtered(
    banned_term: str,
) -> None:
    utterance = "I rebuilt the ingestion pipeline with backpressure controls."
    poisoned = f"You sound {banned_term} to me."
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(
            intent=VoiceIntent.SUBSTANTIVE_ANSWER, confidence=0.9, reaction=poisoned,
        ),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, utterance)

    assert all(poisoned not in segment for segment in reply.segments)


@pytest.mark.parametrize("banned_term", PROTECTED_ATTRIBUTE_TERMS)
def test_poisoned_reactions_referencing_protected_attributes_are_filtered(
    banned_term: str,
) -> None:
    utterance = "I coordinate between design and infrastructure teams weekly."
    poisoned = f"I notice your {banned_term} while you speak."
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(
            intent=VoiceIntent.SUBSTANTIVE_ANSWER, confidence=0.9, reaction=poisoned,
        ),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, utterance)

    assert all(banned_term not in segment.lower() for segment in reply.segments)


def test_poisoned_transition_is_dropped_and_deterministic_segue_used() -> None:
    utterance = "We shipped the feature in six weeks."
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(
            intent=VoiceIntent.SUBSTANTIVE_ANSWER,
            confidence=0.9,
            reaction="Got it.",
            transition="Let's see if you can handle the next challenge.",
        ),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)
    agent.queue.append(NextPrompt(
        state=InterviewState.IN_PROGRESS,
        question_id="q2",
        question_text="Describe a difficult production issue you solved.",
        category=QuestionCategory.PROBLEM_SOLVING,
        question_index=1,
        total_questions=3,
    ))

    reply = service.respond(context, utterance)

    assert not any("handle" in segment for segment in reply.segments)
    assert any("problem solving" in segment for segment in reply.segments)


def test_mock_mode_full_corpus_produces_no_banned_vocabulary_in_any_spoken_segment() -> None:
    """End-to-end over an answer corpus through the REAL mock classifier: no
    spoken segment may contain decision vocabulary, performance commentary, or
    protected-attribute references."""
    from agents.voice_intent_classifier import VoiceIntentClassifier

    classifier = VoiceIntentClassifier(MockLLMService(), timeout_seconds=1.0)
    corpus = (
        "I led the API migration and reduced latency by 40 percent using caching.",
        "Our team of four delivered the payments integration before the deadline.",
        "Honestly I don't have experience with that technology stack.",
        "I'd rather not discuss the specifics of that project.",
        "I'm not sure of the exact number, but we handled around two million requests daily.",
        "We used Python and PostgreSQL for the backend services.",
        "The migration taught me to stage rollouts behind feature flags.",
        "I built the monitoring dashboards and on-call rotation myself.",
        "Answer nine describes measurable throughput improvements at scale.",
        "Answer ten covers the testing strategy, tooling, and release automation.",
    )
    persona = default_persona(company_name="Acme")
    banned = [term.lower() for term in BANNED_VOCABULARY + PROTECTED_ATTRIBUTE_TERMS]

    for index, answer in enumerate(corpus):
        service, agent, context = build_service(persona, classifier)
        agent.next_prompt = NextPrompt(
            state=InterviewState.IN_PROGRESS,
            question_id=f"q{index}",
            question_text=f"Interview question {index}: tell me about your work.",
            category=QuestionCategory.TECHNICAL,
            question_index=index % 2,
            total_questions=10,
        )
        reply = service.respond(context, answer)
        spoken = " ".join(reply.segments).lower()
        for term in banned:
            assert term not in spoken, f"answer {index}: banned term '{term}' spoken"
        assert "thank you for sharing" not in spoken


# ---- candidate-question answers stay deterministic -----------------------------


@pytest.mark.parametrize(("question", "fragment"), [
    ("What's the salary range for this position?", "compensation"),
    ("What are the benefits like?", "benefits"),
    ("What are the next steps in the process?", "human recruiter"),
    ("Is the team remote or in the office?", "the team"),
])
def test_salary_outcome_process_answers_remain_deterministic_not_llm_generated(
    question: str, fragment: str,
) -> None:
    classifier = FakeClassifier({
        question: VoiceIntentResult(
            intent=VoiceIntent.CANDIDATE_QUESTION,
            confidence=0.95,
            # Even if a model tries to smuggle an answer into `reaction`, the
            # dispatcher never speaks classifier text for candidate questions.
            reaction="You'll be richly rewarded, obviously.",
        ),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    reply = service.respond(context, question)

    assert agent.submitted == []
    assert fragment in reply.segments[0].lower() or fragment in reply.segments[0]
    assert "richly rewarded" not in reply.segments[0]


# ---- off-topic bias toward answers ----------------------------------------------


def test_tangential_but_relevant_story_is_treated_as_an_answer() -> None:
    utterance = (
        "It actually started as a side hobby project, but that prototype became "
        "the billing system we shipped — I designed the schema and wrote the "
        "migration path myself."
    )
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(
            intent=VoiceIntent.SUBSTANTIVE_ANSWER, confidence=0.8,
        ),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    service.respond(context, utterance)

    # Bias: when the classifier says it's an answer, it is submitted. A
    # wrongly-discarded answer is permanent evidence loss; a tangent kept is
    # harmless (the evaluator simply finds no relevant evidence in it).
    assert agent.submitted == [utterance]


# ---- repeats: offer to move on instead of looping --------------------------------


def test_too_many_repeats_offer_skipping_ahead_but_keep_answering_the_question() -> None:
    utterance = "Can you repeat the question?"
    classifier = FakeClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.REPEAT_REQUEST, confidence=0.9),
    })
    service, agent, context = build_service(default_persona(company_name="Acme"), classifier)

    service.respond(context, utterance)
    service.respond(context, utterance)
    third = service.respond(context, utterance)

    assert any("skip ahead" in segment for segment in third.segments)
    assert third.segments[-1] == agent.next_prompt.question_text
    assert agent.submitted == []
