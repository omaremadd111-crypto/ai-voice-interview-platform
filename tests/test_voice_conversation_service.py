from types import SimpleNamespace

import pytest

from agents.voice_intent_classifier import VoiceIntentClassificationError
from models.agent_persona import default_persona
from models.common import InterviewState, QuestionCategory
from models.interview import NextPrompt
from models.platform import AgentConfigRecord, CandidateRecord, Position
from models.voice_intent import VoiceIntent, VoiceIntentResult
from services.interview_engine import InvalidInterviewStateError
from application.voice_conversation_service import VoiceConversationService


class FakeAgentService:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.submitted_intent_hints: list[VoiceIntent | None] = []
        self.current_calls = 0
        self.ended = False
        self.next_prompt = NextPrompt(
            state=InterviewState.IN_PROGRESS,
            question_id="q1",
            question_text="Tell me about a project you led?",
            total_questions=2,
        )

    def start_interview(self, session_id: str) -> NextPrompt:
        return self.next_prompt

    def submit_answer(
        self, session_id: str, answer: str, *, intent_hint: VoiceIntent | None = None,
    ) -> NextPrompt:
        self.submitted.append(answer)
        self.submitted_intent_hints.append(intent_hint)
        return self.next_prompt

    def get_current_prompt(self, session_id: str) -> NextPrompt:
        self.current_calls += 1
        return self.next_prompt

    def end_interview(self, session_id: str) -> object:
        self.ended = True
        return object()


class FakeConfigs:
    def __init__(self, configs: list[AgentConfigRecord]) -> None:
        self.configs = configs

    def list(self, *, owner_id: int | None = None, position_id: int | None = None):
        return [config for config in self.configs if config.owner_id == owner_id]


class FakeIntentClassifier:
    """Test double standing in for agents.voice_intent_classifier.VoiceIntentClassifier.

    ``results`` maps the exact utterance text to a canned VoiceIntentResult (or
    an exception instance/class to simulate classifier failure/timeout).
    """

    def __init__(self, results: dict[str, VoiceIntentResult | type[Exception]]) -> None:
        self.results = results
        self.calls: list[tuple[str, str]] = []
        self.call_count = 0

    def classify(
        self, question_text: str, utterance: str, *, upcoming_category: str | None = None,
    ) -> VoiceIntentResult:
        self.calls.append((question_text, utterance))
        self.call_count += 1
        outcome = self.results.get(utterance)
        if outcome is None:
            return VoiceIntentResult(intent=VoiceIntent.SUBSTANTIVE_ANSWER, confidence=0.9)
        if isinstance(outcome, type) and issubclass(outcome, Exception):
            raise outcome("simulated classifier failure")
        return outcome


def build_service(persona=None, intent_classifier=None, intent_min_confidence: float = 0.55):
    agent = FakeAgentService()
    candidate = CandidateRecord(id=4, position_id=3, full_name="Sam Taylor")
    position = Position(id=3, owner_id=2, company_name="Acme", title="Engineer")
    configs = []
    if persona is not None:
        configs.append(
            AgentConfigRecord(
                id=1,
                owner_id=2,
                position_id=3,
                name="Aimy",
                config=persona.model_dump(mode="json"),
            )
        )
    service = VoiceConversationService(
        agent,  # type: ignore[arg-type]
        SimpleNamespace(get=lambda _: candidate),
        SimpleNamespace(get=lambda _: position),
        FakeConfigs(configs),  # type: ignore[arg-type]
        intent_classifier,  # type: ignore[arg-type]
        intent_min_confidence=intent_min_confidence,
    )
    return service, agent, service.load_context(session_id="s1", candidate_id=4, position_id=3)


def test_voice_uses_configured_persona_and_core_prompt() -> None:
    persona = default_persona(agent_name="Nova", company_name="Acme").model_copy(
        update={"tone": "friendly"}
    )
    service, agent, context = build_service(persona)

    opening = service.begin(context)
    reply = service.respond(context, "I led the API migration.")

    assert "Nova" in opening.segments[0]
    assert "Sam" in opening.segments[0]
    assert agent.submitted == ["I led the API migration."]
    assert reply.segments == ("Good example.", agent.next_prompt.question_text)


@pytest.mark.parametrize(
    "filler",
    ("hmm", "uh", "Um...", "well, uh", "erm"),
)
def test_filler_only_speech_waits_without_advancing_core(filler: str) -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.respond(context, filler)

    assert reply.segments == ()
    assert agent.submitted == []
    assert agent.current_calls == 0


def test_incomplete_utterance_is_buffered_until_candidate_finishes() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    incomplete = service.respond(context, "I was responsible for...")
    completed = service.respond(context, "uh, building the API and reducing latency by 30 percent.")

    assert incomplete.segments == ()
    assert agent.submitted == [
        "I was responsible for building the API and reducing latency by 30 percent."
    ]
    assert completed.segments[0] == "Good example."


def test_explicit_thinking_pause_is_acknowledged_once_without_advancing() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    first = service.respond(context, "Uh, let me think")
    repeated = service.respond(context, "Give me a second")

    assert first.segments == ("Take your time.",)
    assert repeated.segments == ()
    assert agent.submitted == []


def test_thinking_lead_in_with_substantive_content_is_still_an_answer() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    service.respond(
        context,
        "Let me think, I handled the outage by fixing a leaked database connection.",
    )

    assert agent.submitted == [
        "Let me think, I handled the outage by fixing a leaked database connection."
    ]


def test_rephrase_request_does_not_advance_interview_core() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    repeated = service.respond(context, "Could you rephrase the question?")

    assert repeated.segments == ("Sure — could you walk me through a project you led?",)
    assert agent.submitted == []
    assert agent.current_calls == 1


def test_off_topic_answer_is_redirected_without_advancing_core() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    redirected = service.respond(context, "Can you tell me a joke?")

    assert "focused on your experience" in redirected.segments[0]
    assert redirected.segments[-1] == agent.next_prompt.question_text
    assert agent.submitted == []
    assert agent.current_calls == 1


def test_candidate_question_uses_verified_role_details_without_advancing() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.respond(context, "What can you tell me about the role?")

    assert "Engineer role at Acme" in reply.segments[0]
    assert "approved posting" in reply.segments[0]
    assert reply.segments[-1] == agent.next_prompt.question_text
    assert agent.submitted == []


def test_answer_that_begins_with_what_i_did_is_not_a_candidate_question() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    service.respond(context, "What I did in that job was redesign the deployment process.")

    assert agent.submitted == [
        "What I did in that job was redesign the deployment process."
    ]


def test_candidate_question_defers_when_verified_company_information_is_unavailable() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.respond(context, "What benefits does the company offer?")

    assert "don't have verified information about benefits" in reply.segments[0]
    assert "don't want to guess" in reply.segments[0]
    assert "HR" in reply.segments[0]
    assert agent.submitted == []


def test_request_to_ask_a_question_gets_a_natural_invitation() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.respond(context, "Can I ask a question?")

    assert reply.segments == ("Of course. What would you like to know?",)
    assert agent.submitted == []
    assert agent.current_calls == 0


def test_acknowledgements_are_varied_and_not_used_after_every_answer() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    replies = [
        service.respond(context, f"My relevant answer number {index} covers the work clearly.")
        for index in range(1, 6)
    ]

    acknowledgements = [
        reply.segments[0]
        for reply in replies
        if len(reply.segments) > 1
    ]
    assert acknowledgements == ["Good example.", "That makes sense.", "Got it."]
    assert len(acknowledgements) < len(replies)
    assert len(set(acknowledgements)) == len(acknowledgements)
    assert all(
        "Thank you for sharing" not in segment
        for reply in replies
        for segment in reply.segments
    )


def test_follow_up_moves_directly_to_evidence_gap_without_acknowledgement() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))
    agent.next_prompt = agent.next_prompt.model_copy(
        update={"question_text": "What result did that change produce?", "is_follow_up": True}
    )

    reply = service.respond(context, "I built the service in Python.")

    assert reply.segments == ("What result did that change produce?",)
    assert agent.submitted == ["I built the service in Python."]


def test_overloaded_approved_question_is_spoken_as_one_clear_question() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))
    agent.next_prompt = agent.next_prompt.model_copy(
        update={
            "question_text": (
                "How did you design the API, and what trade-offs did you make, "
                "and how did you measure the result?"
            )
        }
    )

    reply = service.respond(context, "I designed an API for our billing service.")

    assert reply.segments[-1] == "How did you design the API?"


def test_closing_question_uses_natural_company_specific_wording() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))
    agent.next_prompt = agent.next_prompt.model_copy(
        update={"category": QuestionCategory.CLOSING, "question_text": "Any questions?"}
    )

    reply = service.respond(context, "I completed the API migration successfully.")

    assert reply.segments == (
        "Before we wrap up, what questions do you have about the role, the team, or Acme?",
    )


def test_candidate_question_during_closing_returns_to_closing_invitation() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))
    agent.next_prompt = agent.next_prompt.model_copy(
        update={"category": QuestionCategory.CLOSING, "question_text": "Any questions?"}
    )

    reply = service.respond(context, "What does the team structure look like?")

    assert "don't have verified information about the team" in reply.segments[0]
    assert reply.segments[-1] == (
        "Before we wrap up, what questions do you have about the role, the team, or Acme?"
    )
    assert agent.submitted == []


def test_candidate_can_end_and_receives_configured_closing() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.respond(context, "stop the interview")

    assert reply.finished is True
    assert agent.ended is True
    assert "Sam" in reply.segments[0]


# ---- Phase 1: semantic dont_know / genuine_refusal understanding ----

DONT_KNOW_PARAPHRASES = (
    "I don't know.",
    "I haven't worked with that.",
    "I'm not familiar with Kubernetes.",
    "I don't really have experience with this.",
    "I haven't had the chance to use that technology.",
    "That's not something I've worked on before.",
)


@pytest.mark.parametrize("utterance", DONT_KNOW_PARAPHRASES)
def test_semantic_classifier_suppresses_follow_up_on_dont_know_paraphrases(utterance: str) -> None:
    classifier = FakeIntentClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.DONT_KNOW, confidence=0.95),
    })
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )

    reply = service.respond(context, utterance)

    # Transcript fidelity: the candidate's literal words are recorded, never
    # dropped or rewritten, and no follow-up is forced onto a topic they said
    # they don't know.
    assert agent.submitted == [utterance]
    assert agent.submitted_intent_hints == [VoiceIntent.DONT_KNOW]
    assert reply.segments[0] == "No problem, let's move on."
    assert reply.segments[-1] == agent.next_prompt.question_text
    assert not any("Thank you for sharing" in segment for segment in reply.segments)


def test_semantic_classifier_recognises_genuine_refusal_distinctly() -> None:
    utterance = "I'd rather not discuss that."
    classifier = FakeIntentClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.GENUINE_REFUSAL, confidence=0.9),
    })
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )

    reply = service.respond(context, utterance)

    assert agent.submitted == [utterance]
    assert agent.submitted_intent_hints == [VoiceIntent.GENUINE_REFUSAL]
    assert reply.segments[0] == "No problem, let's move on."


def test_classifier_reaction_text_is_used_when_present() -> None:
    utterance = "I'm not familiar with Kubernetes."
    classifier = FakeIntentClassifier({
        utterance: VoiceIntentResult(
            intent=VoiceIntent.DONT_KNOW, confidence=0.97, reaction="No worries at all, next one.",
        ),
    })
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )

    reply = service.respond(context, utterance)

    assert reply.segments[0] == "No worries at all, next one."


def test_dont_know_fallback_reactions_do_not_repeat_back_to_back() -> None:
    classifier = FakeIntentClassifier({
        "I don't know the first one.": VoiceIntentResult(intent=VoiceIntent.DONT_KNOW, confidence=0.9),
        "I don't know the second one either.": VoiceIntentResult(
            intent=VoiceIntent.DONT_KNOW, confidence=0.9,
        ),
    })
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )

    first = service.respond(context, "I don't know the first one.")
    second = service.respond(context, "I don't know the second one either.")

    assert first.segments[0] != second.segments[0]


def test_hedge_then_substance_is_not_routed_as_dont_know() -> None:
    utterance = "I'm not totally sure of the exact number, but we handled around two million requests."
    classifier = FakeIntentClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.SUBSTANTIVE_ANSWER, confidence=0.95),
    })
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )

    reply = service.respond(context, utterance)

    assert agent.submitted == [utterance]
    assert agent.submitted_intent_hints == [VoiceIntent.SUBSTANTIVE_ANSWER]
    assert reply.segments[0] != "No problem, let's move on."


def test_low_confidence_classification_falls_back_to_legacy_gate() -> None:
    utterance = "I'm not familiar with Kubernetes."
    classifier = FakeIntentClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.DONT_KNOW, confidence=0.2),
    })
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier, intent_min_confidence=0.55,
    )

    reply = service.respond(context, utterance)

    # Below threshold: treated as if classification never happened. The answer
    # still reaches the engine untouched (transcript fidelity), but Phase 1's
    # dont_know handling does not activate on a low-confidence guess.
    assert agent.submitted == [utterance]
    assert agent.submitted_intent_hints == [None]
    assert reply.segments[0] != "No problem, let's move on."


def test_classifier_failure_falls_back_to_legacy_behaviour_without_crashing() -> None:
    utterance = "I'm not familiar with Kubernetes."
    classifier = FakeIntentClassifier({utterance: VoiceIntentClassificationError})
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )

    reply = service.respond(context, utterance)

    assert agent.submitted == [utterance]
    assert agent.submitted_intent_hints == [None]
    assert reply.segments[-1] == agent.next_prompt.question_text


def test_semantic_layer_kill_switch_reproduces_pre_phase1_behaviour() -> None:
    """No classifier constructed (VOICE_SEMANTIC_LAYER_ENABLED=false in prod) ==
    every paraphrase except the exact regex match falls through as an ordinary
    answer, exactly like the pre-Phase-1 system -- proving the rollback lever
    genuinely reverts behaviour rather than merely existing on paper."""
    utterance = "I'm not familiar with Kubernetes."
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.respond(context, utterance)

    assert agent.submitted == [utterance]
    assert agent.submitted_intent_hints == [None]
    assert reply.segments[0] != "No problem, let's move on."


# ---- P0-2: never orphan a question the engine has already committed to ----
#
# question_must_be_delivered marks a VoiceReply whose LAST segment is a
# question the engine already advanced to (start_interview/submit_answer ran
# before these segments were computed). LiveKit's _speak_reply protects that
# segment from being skipped or cut off; these tests confirm the flag is set
# (or correctly left False) at every VoiceConversationService call site.


def test_begin_marks_the_opening_question_as_must_be_delivered() -> None:
    """start_interview already commits the engine to question 1 before the
    opening plays -- same orphaning risk as a mid-interview submit_answer."""
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.begin(context)

    assert reply.question_must_be_delivered is True
    assert reply.segments[-1] == agent.next_prompt.question_text


def test_submitted_answer_marks_the_next_question_as_must_be_delivered() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.respond(context, "I led the API migration.")

    assert reply.question_must_be_delivered is True
    assert reply.segments[-1] == agent.next_prompt.question_text


def test_dont_know_reply_also_marks_its_question_as_must_be_delivered() -> None:
    """Even the dont_know/genuine_refusal reaction path still calls
    submit_answer first -- the engine has already advanced, so the question it
    lands on must be just as protected as the ordinary-answer path."""
    utterance = "I'm not familiar with Kubernetes."
    classifier = FakeIntentClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.DONT_KNOW, confidence=0.95),
    })
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )

    reply = service.respond(context, utterance)

    assert reply.question_must_be_delivered is True


def test_closing_reply_does_not_mark_a_question_as_must_be_delivered() -> None:
    """The closing script is not an engine question -- protecting the FULL
    closing sequence from barge-in is a separate, already-handled concern
    (P7/P7.5's own completion ordering), not part of P0-2's scope."""
    service, agent, context = build_service(default_persona(company_name="Acme"))
    agent.next_prompt = agent.next_prompt.model_copy(update={"finished": True})

    reply = service.respond(context, "I led the API migration.")

    assert reply.finished is True
    assert reply.question_must_be_delivered is False


def test_no_response_resolution_marks_its_question_as_must_be_delivered() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.resolve_no_response(context, nudge_count=2, elapsed_seconds=45.0)

    assert reply.question_must_be_delivered is True
    assert reply.segments[-1] == agent.next_prompt.question_text


def test_no_response_with_no_question_left_speaks_nothing_and_is_not_marked() -> None:
    """Interview already ended while the silence ladder was ticking: nothing is
    submitted and there is no question to protect."""
    _, _, context = build_service(default_persona(company_name="Acme"))

    class EndedAgentService(FakeAgentService):
        def get_current_prompt(self, session_id: str) -> NextPrompt:
            raise InvalidInterviewStateError("interview already completed")

    service = VoiceConversationService(
        EndedAgentService(),  # type: ignore[arg-type]
        SimpleNamespace(get=lambda _: None),
        SimpleNamespace(get=lambda _: None),
        FakeConfigs([]),  # type: ignore[arg-type]
    )

    reply = service.resolve_no_response(context, nudge_count=1, elapsed_seconds=45.0)

    assert reply.segments == ()
    assert reply.question_must_be_delivered is False


@pytest.mark.parametrize(
    "utterance",
    (
        "can you repeat the question",
        "what's the salary range",
        "tell me a joke",
        "give me a second",
    ),
)
def test_meta_intent_replies_never_mark_their_question_as_must_be_delivered(
    utterance: str,
) -> None:
    """Repeat/candidate-question/off-topic/pause replies never call
    submit_answer -- the engine's current question is unchanged, so dropping
    the re-spoken question on interruption is harmless (byte-identical to
    before P0-2)."""
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.respond(context, utterance)

    assert agent.submitted == []
    assert reply.question_must_be_delivered is False


def test_restate_question_never_marks_its_question_as_must_be_delivered() -> None:
    """Presence-retry re-speaks the CURRENT question without touching the
    engine -- no state advanced, so nothing needs protecting."""
    service, agent, context = build_service(default_persona(company_name="Acme"))

    reply = service.restate_question(context)

    assert reply.question_must_be_delivered is False


# ---- P0-4: end-to-end latency instrumentation ----
#
# VoiceConversationService measures classify_ms/engine_ms itself and stashes
# them per session for the LiveKit layer to retrieve via pop_turn_timings()
# and combine into one VOICE_TURN_LATENCY event alongside its own
# TTS-side measurement. These tests check the measurement/bookkeeping in
# isolation from the LiveKit layer.


_NO_TIMINGS: dict[str, float | None] = {
    "classify_ms": None,
    "engine_ms": None,
    "prompt_lookup_ms": None,
    "category_lookup_ms": None,
}


def test_pop_turn_timings_defaults_to_all_none_for_an_unknown_session() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    timings = service.pop_turn_timings(context.session_id)

    assert timings == _NO_TIMINGS


def test_ordinary_answer_records_engine_ms_but_not_classify_ms() -> None:
    """No classifier constructed (kill switch off) -- the classifier is never
    invoked at all, so classify_ms (and the classification-only
    category_lookup_ms) stay None; submit_answer still runs, so engine_ms is
    a real, non-negative number. prompt_lookup_ms is also real: with no
    cached prompt to reuse, _submit_and_reply still fetches prompt_before
    itself, exactly as before Batch 2 -- only now that fetch is timed."""
    service, agent, context = build_service(default_persona(company_name="Acme"))

    service.respond(context, "I led the API migration.")
    timings = service.pop_turn_timings(context.session_id)

    assert timings["classify_ms"] is None
    assert timings["category_lookup_ms"] is None
    assert isinstance(timings["engine_ms"], float)
    assert timings["engine_ms"] >= 0.0
    assert isinstance(timings["prompt_lookup_ms"], float)
    assert timings["prompt_lookup_ms"] >= 0.0
    assert agent.current_calls == 1


def test_successful_classification_records_classify_ms() -> None:
    utterance = "I'm not familiar with Kubernetes."
    classifier = FakeIntentClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.DONT_KNOW, confidence=0.95),
    })
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )

    service.respond(context, utterance)
    timings = service.pop_turn_timings(context.session_id)

    assert isinstance(timings["classify_ms"], float)
    assert timings["classify_ms"] >= 0.0
    # dont_know still calls submit_answer -- engine_ms is recorded too.
    assert isinstance(timings["engine_ms"], float)
    # Batch 2: the ONE get_current_prompt fetch taken for classification is
    # reused as _submit_and_reply's prompt_before -- no second store round
    # trip for this turn, proven by the fake's own call counter.
    assert isinstance(timings["prompt_lookup_ms"], float)
    assert agent.current_calls == 1


def test_failed_classification_still_records_classify_ms() -> None:
    """A classifier timeout/error still cost real wall-clock time -- that
    latency is part of what the candidate experienced, so it is recorded even
    though the classification itself produced no usable result."""
    utterance = "I'm not familiar with Kubernetes."
    classifier = FakeIntentClassifier({utterance: VoiceIntentClassificationError})
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )

    service.respond(context, utterance)
    timings = service.pop_turn_timings(context.session_id)

    assert isinstance(timings["classify_ms"], float)
    # The prompt fetched before the (failed) classification attempt is still
    # reused for submit_and_reply's prompt_before -- one store call, not two.
    assert isinstance(timings["prompt_lookup_ms"], float)
    assert agent.current_calls == 1


def test_stop_request_records_engine_ms_for_end_interview() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    service.respond(context, "stop the interview")
    timings = service.pop_turn_timings(context.session_id)

    assert isinstance(timings["engine_ms"], float)
    assert timings["engine_ms"] >= 0.0
    # The exact-phrase stop fast path ends the interview without ever needing
    # the current question.
    assert timings["prompt_lookup_ms"] is None


def test_meta_intent_reply_records_no_classify_or_engine_stage() -> None:
    """Repeat/off-topic/candidate-question/pause never touch the classifier
    (kill switch off here) or the engine -- those two stages stay None.
    prompt_lookup_ms is populated though (Batch 2 instrumentation): the
    legacy repeat-request handling still needs the current question to speak
    it back, exactly as before -- that pre-existing fetch is simply timed
    now, not eliminated (only the classifier-path duplicate fetch was)."""
    service, agent, context = build_service(default_persona(company_name="Acme"))

    service.respond(context, "can you repeat the question")
    timings = service.pop_turn_timings(context.session_id)

    assert timings["classify_ms"] is None
    assert timings["engine_ms"] is None
    assert timings["category_lookup_ms"] is None
    assert isinstance(timings["prompt_lookup_ms"], float)
    assert agent.current_calls == 1


def test_meta_intent_via_classifier_reuses_the_classification_time_prompt() -> None:
    """The semantic-dispatch meta-intent path (_dispatch_semantic) used to
    re-fetch get_current_prompt even though classification had just fetched
    the identical, still-unchanged prompt. Batch 2 reuses it instead --
    proven here by the fake's call counter staying at 1 for a full
    classifier-driven off-topic turn."""
    utterance = "Can you tell me a joke?"
    classifier = FakeIntentClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.OFF_TOPIC, confidence=0.95),
    })
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )

    reply = service.respond(context, utterance)
    timings = service.pop_turn_timings(context.session_id)

    assert agent.submitted == []
    assert reply.segments[-1] == agent.next_prompt.question_text
    assert agent.current_calls == 1
    assert isinstance(timings["prompt_lookup_ms"], float)
    assert timings["engine_ms"] is None


def test_upcoming_category_lookup_time_is_measured_separately() -> None:
    """category_lookup_ms is its own stage, distinct from prompt_lookup_ms --
    both are session-store round trips, but different ones."""
    utterance = "I led the API migration."
    classifier = FakeIntentClassifier({
        utterance: VoiceIntentResult(intent=VoiceIntent.SUBSTANTIVE_ANSWER, confidence=0.9),
    })
    service, agent, context = build_service(
        default_persona(company_name="Acme"), intent_classifier=classifier,
    )
    agent.get_upcoming_category = lambda session_id: QuestionCategory.TECHNICAL  # type: ignore[attr-defined]

    service.respond(context, utterance)
    timings = service.pop_turn_timings(context.session_id)

    assert isinstance(timings["category_lookup_ms"], float)
    assert timings["category_lookup_ms"] >= 0.0
    assert isinstance(timings["prompt_lookup_ms"], float)


def test_pop_turn_timings_clears_after_reading() -> None:
    service, agent, context = build_service(default_persona(company_name="Acme"))

    service.respond(context, "I led the API migration.")
    first = service.pop_turn_timings(context.session_id)
    second = service.pop_turn_timings(context.session_id)

    assert first["engine_ms"] is not None
    assert second == _NO_TIMINGS


def test_stale_timings_never_leak_into_a_turn_that_does_not_remeasure_them() -> None:
    """respond() clears prior bookkeeping unconditionally at the start (not
    only via pop_turn_timings): a caller that does not pop between turns must
    never see an earlier turn's engine_ms attributed to a later fast-path turn
    that touched neither the classifier nor the engine."""
    service, agent, context = build_service(default_persona(company_name="Acme"))

    service.respond(context, "I led the API migration.")  # records engine_ms
    service.respond(context, "hmm")  # pure filler: fast path, no engine call

    timings = service.pop_turn_timings(context.session_id)

    assert timings == _NO_TIMINGS
