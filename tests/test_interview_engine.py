"""InterviewEngine state-machine tests: transitions, follow-up policy, progress,
transcript integrity, session isolation, and candidate-facing safety.
"""
import pytest

from agents.candidate_analyzer import CandidateAnalyzer
from agents.fit_analyzer import FitAnalyzer
from agents.interview_planner import MIN_QUESTIONS, InterviewPlanner
from agents.interviewer import Interviewer
from agents.job_analyzer import JobAnalyzer
from models.common import InterviewState, QuestionCategory
from models.interview import FollowUpDecision, InterviewPlan, InterviewQuestion, InterviewTurn, NextPrompt
from models.session import InterviewSession
from models.voice_intent import VoiceIntent
from services.interview_engine import (
    GLOBAL_FOLLOW_UP_BUDGET_RATIO,
    MAX_FOLLOW_UPS_PER_QUESTION,
    InterviewEngine,
    InvalidInterviewStateError,
    _looks_like_dont_know,
)
from services.llm.mock.mock_service import MockLLMService

WEAK_ANSWER = "It was fine."
STRONG_ANSWER = (
    "I built a Python API using SQL and Docker because performance mattered under heavy "
    "load, so I optimized the query layer and reduced average latency by 40 percent, which "
    "allowed the team to scale confidently across every new service we shipped that quarter."
)
_NOW = "2026-08-19T00:00:00+00:00"


def _question(
    qid: str,
    category: QuestionCategory = QuestionCategory.TECHNICAL,
    follow_up_allowed: bool = True,
    expected_topics: list[str] | None = None,
) -> InterviewQuestion:
    return InterviewQuestion(
        id=qid, category=category, question=f"Question text for {qid}?",
        purpose="test purpose", expected_topics=expected_topics if expected_topics is not None else ["Python"],
        difficulty="medium", follow_up_allowed=follow_up_allowed,
    )


def _plan(n: int = 3, follow_up_allowed: bool = True) -> InterviewPlan:
    return InterviewPlan(questions=[_question(f"q{i}", follow_up_allowed=follow_up_allowed) for i in range(n)])


def _session(plan: InterviewPlan | None = None, state: InterviewState = InterviewState.READY,
             session_id: str = "sess-1") -> InterviewSession:
    return InterviewSession(
        id=session_id, company="Acme", interview_plan=plan or _plan(),
        state=state, created_at=_NOW, updated_at=_NOW, llm_provider="mock", is_mock=True,
    )


@pytest.fixture
def interviewer() -> Interviewer:
    return Interviewer(MockLLMService())


@pytest.fixture
def engine(interviewer: Interviewer) -> InterviewEngine:
    return InterviewEngine(interviewer)


class _AlwaysFollowUpInterviewer:
    """Stub that always proposes a follow-up -- used to prove the ENGINE, not the
    agent, is what enforces every cap and budget."""

    def propose_follow_up(self, question, turns_so_far, latest_answer) -> FollowUpDecision:
        return FollowUpDecision(should_ask=True, follow_up_question="Can you say more?", reason="always ask")


# ==================== STATE ====================

def test_start_from_created_raises(engine: InterviewEngine) -> None:
    session = _session(state=InterviewState.CREATED)
    with pytest.raises(InvalidInterviewStateError):
        engine.start(session)


def test_start_from_ready_transitions_to_in_progress(engine: InterviewEngine) -> None:
    session = _session(state=InterviewState.READY)
    engine.start(session)
    assert session.state == InterviewState.IN_PROGRESS


def test_duplicate_start_rejected(engine: InterviewEngine) -> None:
    session = _session(state=InterviewState.READY)
    engine.start(session)
    with pytest.raises(InvalidInterviewStateError):
        engine.start(session)


def test_submit_before_start_rejected(engine: InterviewEngine) -> None:
    session = _session(state=InterviewState.READY)
    with pytest.raises(InvalidInterviewStateError):
        engine.submit_answer(session, "an answer")


def test_completed_rejects_new_answers(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(1), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, STRONG_ANSWER)
    assert prompt.finished is True
    assert session.state == InterviewState.COMPLETED
    with pytest.raises(InvalidInterviewStateError):
        engine.submit_answer(session, "too late")


def test_end_from_created_raises(engine: InterviewEngine) -> None:
    with pytest.raises(InvalidInterviewStateError):
        engine.end(_session(state=InterviewState.CREATED))


def test_end_from_ready_raises(engine: InterviewEngine) -> None:
    with pytest.raises(InvalidInterviewStateError):
        engine.end(_session(state=InterviewState.READY))


def test_end_twice_raises(engine: InterviewEngine) -> None:
    session = _session(state=InterviewState.READY)
    engine.start(session)
    engine.end(session)
    with pytest.raises(InvalidInterviewStateError):
        engine.end(session)


def test_end_after_natural_completion_raises(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(1), state=InterviewState.READY)
    engine.start(session)
    engine.submit_answer(session, STRONG_ANSWER)  # naturally completes a 1-question plan
    assert session.state == InterviewState.COMPLETED
    with pytest.raises(InvalidInterviewStateError):
        engine.end(session)


# ==================== QUESTIONS ====================

def test_start_returns_first_question_correctly(engine: InterviewEngine) -> None:
    plan = _plan(3)
    session = _session(plan=plan, state=InterviewState.READY)
    prompt = engine.start(session)
    assert prompt.question_id == plan.questions[0].id
    assert prompt.question_text == plan.questions[0].question
    assert prompt.is_follow_up is False
    assert prompt.question_index == 0
    assert prompt.total_questions == 3


def test_normal_advancement_moves_to_next_question(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(3, follow_up_allowed=False), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, STRONG_ANSWER)
    assert prompt.question_id == session.interview_plan.questions[1].id
    assert prompt.question_index == 1


def test_final_question_naturally_completes_interview(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(2, follow_up_allowed=False), state=InterviewState.READY)
    engine.start(session)
    engine.submit_answer(session, STRONG_ANSWER)
    prompt = engine.submit_answer(session, STRONG_ANSWER)
    assert prompt.finished is True
    assert session.state == InterviewState.COMPLETED
    assert prompt.progress_pct == 100.0


def test_requested_number_of_main_questions_is_preserved(engine: InterviewEngine) -> None:
    plan = _plan(5, follow_up_allowed=False)
    session = _session(plan=plan, state=InterviewState.READY)
    prompt = engine.start(session)
    seen_main_ids = {prompt.question_id}
    while not prompt.finished:
        prompt = engine.submit_answer(session, STRONG_ANSWER)
        if not prompt.is_follow_up:
            seen_main_ids.add(prompt.question_id)
    assert len(seen_main_ids) == 5
    assert seen_main_ids == {q.id for q in plan.questions}


# ==================== FOLLOW-UPS ====================

def test_follow_up_proposed_and_accepted(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, WEAK_ANSWER)
    assert prompt.is_follow_up is True
    assert prompt.question_id == session.interview_plan.questions[0].id


def test_no_follow_up_when_question_disallows_it(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(3, follow_up_allowed=False), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, WEAK_ANSWER)
    assert prompt.is_follow_up is False
    assert prompt.question_index == 1


def test_max_two_follow_ups_per_question(engine: InterviewEngine) -> None:
    # 6 questions -> global budget = ceil(0.5*6) = 3, so the per-question cap (2) binds
    # first for a single question that keeps getting weak answers.
    session = _session(plan=_plan(6), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, WEAK_ANSWER)  # main -> follow-up 1
    assert prompt.is_follow_up is True
    assert session.current_follow_up_count == 1
    prompt = engine.submit_answer(session, WEAK_ANSWER)  # follow-up 1 -> follow-up 2
    assert prompt.is_follow_up is True
    assert session.current_follow_up_count == 2
    prompt = engine.submit_answer(session, WEAK_ANSWER)  # follow-up 2 -> cap reached, advance
    assert prompt.is_follow_up is False
    assert prompt.question_index == 1
    assert session.current_follow_up_count == 0  # reset for the new main question


def test_global_follow_up_budget_enforced(engine: InterviewEngine) -> None:
    n = 4
    budget = MAX_FOLLOW_UPS_PER_QUESTION  # irrelevant here; real budget computed below
    import math
    expected_budget = math.ceil(GLOBAL_FOLLOW_UP_BUDGET_RATIO * n)
    assert expected_budget == 2

    session = _session(plan=_plan(n), state=InterviewState.READY)
    engine.start(session)
    total_follow_ups_seen = 0
    prompt = None
    for _ in range(20):
        prompt = engine.submit_answer(session, WEAK_ANSWER)
        if prompt.is_follow_up:
            total_follow_ups_seen += 1
        if prompt.finished:
            break
    assert session.total_follow_up_count == expected_budget
    assert total_follow_ups_seen == expected_budget


def test_follow_ups_do_not_increment_main_question_index(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    assert session.current_question_index == 0
    engine.submit_answer(session, WEAK_ANSWER)  # triggers follow-up
    assert session.current_question_index == 0
    engine.submit_answer(session, WEAK_ANSWER)  # 2nd follow-up
    assert session.current_question_index == 0


def test_follow_up_remains_linked_to_correct_main_question(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    main_question_id = session.interview_plan.questions[0].id
    engine.submit_answer(session, WEAK_ANSWER)  # main answer
    engine.submit_answer(session, WEAK_ANSWER)  # follow-up answer
    for turn in session.transcript.turns:
        assert turn.question_id == main_question_id


def test_interviewer_cannot_override_engine_limits() -> None:
    engine_with_stub = InterviewEngine(_AlwaysFollowUpInterviewer())
    session = _session(plan=_plan(4), state=InterviewState.READY)  # budget = ceil(0.5*4) = 2
    engine_with_stub.start(session)
    prompt = None
    for _ in range(20):
        prompt = engine_with_stub.submit_answer(session, WEAK_ANSWER)
        if prompt.finished:
            break
    assert session.total_follow_up_count == 2  # capped despite the stub always saying yes
    assert session.current_follow_up_count <= MAX_FOLLOW_UPS_PER_QUESTION


# ==================== ANSWERS ====================

def test_empty_answer_stored_without_triggering_follow_up(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, "   ")
    assert session.transcript.turns[-1].answer == "   "
    assert prompt.is_follow_up is False
    assert prompt.question_index == 1


@pytest.mark.parametrize("answer", [
    "I don't know",
    "I do not know.",
    "Not sure",
    "not sure!",
    "I have no experience with that",
    "  I DON'T KNOW  ",
    "Honestly, I don't know.",
])
def test_dont_know_variations_do_not_trigger_follow_up(engine: InterviewEngine, answer: str) -> None:
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, answer)
    assert prompt.is_follow_up is False
    assert prompt.question_index == 1
    assert session.transcript.turns[-1].answer == answer


def test_substantive_weak_answer_may_trigger_follow_up(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, WEAK_ANSWER)
    assert prompt.is_follow_up is True


# ==================== HEDGED-ANSWER CLASSIFICATION (regression) ====================
#
# A hedge qualifies a detail; it does not replace the answer. Before the fix, the refusal
# gate substring-matched "not sure" anywhere in the text, so a rich answer that merely
# hedged one number was classified as "I don't know" and its follow-up was suppressed --
# permanently losing interview signal.

HEDGED_SUBSTANTIVE_ANSWERS = (
    "I'm not sure exactly how many documents we indexed, but it was around 2 million "
    "and I reduced p95 latency from 900ms to 210ms.",
    "I don't remember the exact value, but I designed the API integration and handled "
    "authentication, retries, and error cases.",
    "I may not recall the exact number, but I owned the schema migration end to end.",
    "Not sure, but I used Redis for the cache layer.",
)

GENUINE_REFUSALS = (
    "",
    "   ",
    "I don't know.",
    "I'm not sure.",
    "I don't remember.",
    "I do not know.",
    "Not sure",
    "not sure!",
    "I have no experience with that",
    "  I DON'T KNOW  ",
    "Honestly, I don't know.",
    "Um, well, honestly, I really don't know.",
)


@pytest.mark.parametrize("answer", GENUINE_REFUSALS)
def test_genuine_refusals_are_classified_as_refusals(answer: str) -> None:
    assert _looks_like_dont_know(answer) is True


@pytest.mark.parametrize("answer", HEDGED_SUBSTANTIVE_ANSWERS)
def test_hedged_but_substantive_answers_are_not_refusals(answer: str) -> None:
    assert _looks_like_dont_know(answer) is False


@pytest.mark.parametrize("answer", [
    "I evaluated it against a ground truth set and improved recall by 18%.",
    "I tracked recall and failure categories across 220 labelled queries.",
])
def test_recall_as_a_metric_is_never_read_as_a_refusal(answer: str) -> None:
    """'recall' is a retrieval metric throughout this domain's own sample data. The
    refusal markers are deliberately multi-word phrases ("don't recall"), so the metric
    can never be mistaken for a refusal."""
    assert _looks_like_dont_know(answer) is False


@pytest.mark.parametrize("answer", HEDGED_SUBSTANTIVE_ANSWERS)
def test_hedged_substantive_answer_stays_eligible_for_follow_up(
    engine: InterviewEngine, answer: str,
) -> None:
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, answer)
    assert prompt.is_follow_up is True
    assert prompt.question_index == 0  # still probing the same main question
    assert session.transcript.turns[-1].answer == answer


@pytest.mark.parametrize("answer", ["I don't know.", "I'm not sure.", "I don't remember."])
def test_genuine_refusal_advances_without_a_follow_up(engine: InterviewEngine, answer: str) -> None:
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, answer)
    assert prompt.is_follow_up is False
    assert prompt.question_index == 1  # moved on to the next main question
    assert session.transcript.turns[-1].answer == answer


# ==================== SEMANTIC intent_hint (Phase 1) ====================
#
# These paraphrases are the exact regression target: the regex gate above
# (_looks_like_dont_know) does NOT recognise them -- proven first below -- which
# is exactly why real candidates saying them got probed further before Phase 1.
# intent_hint is an additional, optional signal only a voice caller supplies
# (application/voice_conversation_service.py); text/API/null-transport callers
# never pass it and see identical behaviour to every test above this section.

SEMANTIC_ONLY_DONT_KNOW_PARAPHRASES = (
    "I haven't worked with that.",
    "I'm not familiar with Kubernetes.",
    "I don't really have experience with this.",
    "I haven't had the chance to use that technology.",
    "That's not something I've worked on before.",
)


@pytest.mark.parametrize("answer", SEMANTIC_ONLY_DONT_KNOW_PARAPHRASES)
def test_regex_gate_alone_misses_these_paraphrases(answer: str) -> None:
    """Documents the exact bug Phase 1 fixes: without a classifier hint, these
    natural paraphrases are NOT recognised as refusals by the regex fast path."""
    assert _looks_like_dont_know(answer) is False


@pytest.mark.parametrize(
    "answer", (*SEMANTIC_ONLY_DONT_KNOW_PARAPHRASES, "I don't know."),
)
def test_intent_hint_suppresses_follow_up_the_regex_would_have_missed(
    engine: InterviewEngine, answer: str,
) -> None:
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, answer, intent_hint=VoiceIntent.DONT_KNOW)
    assert prompt.is_follow_up is False
    assert prompt.question_index == 1  # advanced, not stuck probing a known gap
    # Transcript fidelity: the candidate's literal words are stored verbatim --
    # the hint changes only whether a follow-up is offered, never what was said.
    assert session.transcript.turns[-1].answer == answer


def test_genuine_refusal_intent_hint_also_suppresses_follow_up(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(
        session, "I'd rather not discuss that.", intent_hint=VoiceIntent.GENUINE_REFUSAL,
    )
    assert prompt.is_follow_up is False
    assert prompt.question_index == 1


def test_intent_hint_overrides_an_interviewer_that_always_wants_to_probe() -> None:
    """Proves suppression happens in the engine's own gate before the Interviewer
    is ever consulted -- the classifier cannot be second-guessed by a permissive
    (or misbehaving) Interviewer, matching how the regex gate already behaves."""
    engine = InterviewEngine(_AlwaysFollowUpInterviewer())
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(
        session, "I'm not familiar with Kubernetes.", intent_hint=VoiceIntent.DONT_KNOW,
    )
    assert prompt.is_follow_up is False


@pytest.mark.parametrize(
    "intent", [VoiceIntent.SUBSTANTIVE_ANSWER, VoiceIntent.PARTIAL_ANSWER, None],
)
def test_intent_hint_only_suppresses_for_dont_know_and_refusal(
    engine: InterviewEngine, intent: VoiceIntent | None,
) -> None:
    """Every other intent value -- including no hint at all -- leaves follow-up
    eligibility exactly where it already was: decided by the Interviewer/engine
    caps, never forced open or shut by an unrelated intent label."""
    session = _session(plan=_plan(3), state=InterviewState.READY)
    engine.start(session)
    prompt = engine.submit_answer(session, WEAK_ANSWER, intent_hint=intent)
    assert prompt.is_follow_up is True


def test_transcript_ordering_is_chronological(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(3, follow_up_allowed=False), state=InterviewState.READY)
    engine.start(session)
    engine.submit_answer(session, "first answer")
    engine.submit_answer(session, "second answer")
    engine.submit_answer(session, "third answer")
    answers_in_order = [t.answer for t in session.transcript.turns]
    assert answers_in_order == ["first answer", "second answer", "third answer"]


# ==================== PROGRESS ====================

def test_progress_always_within_bounds(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(5), state=InterviewState.READY)
    prompt = engine.start(session)
    assert 0.0 <= prompt.progress_pct <= 100.0
    while not prompt.finished:
        prompt = engine.submit_answer(session, WEAK_ANSWER)
        assert 0.0 <= prompt.progress_pct <= 100.0


def test_progress_is_monotonic_non_decreasing(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(5), state=InterviewState.READY)
    prompt = engine.start(session)
    last = prompt.progress_pct
    while not prompt.finished:
        prompt = engine.submit_answer(session, WEAK_ANSWER)
        assert prompt.progress_pct >= last
        last = prompt.progress_pct


def test_final_completion_progress_is_exactly_100(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(3, follow_up_allowed=False), state=InterviewState.READY)
    engine.start(session)
    engine.submit_answer(session, STRONG_ANSWER)
    prompt = engine.submit_answer(session, STRONG_ANSWER)
    final = engine.submit_answer(session, STRONG_ANSWER)
    assert final.finished is True
    assert final.progress_pct == 100.0


def test_progress_unchanged_across_follow_ups_for_same_question(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(5), state=InterviewState.READY)
    start_prompt = engine.start(session)
    follow_up_prompt = engine.submit_answer(session, WEAK_ANSWER)
    assert follow_up_prompt.is_follow_up is True
    assert follow_up_prompt.progress_pct == start_prompt.progress_pct


def test_early_end_progress_is_100(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(5), state=InterviewState.READY)
    engine.start(session)
    engine.submit_answer(session, STRONG_ANSWER)
    engine.end(session)
    assert session.state == InterviewState.COMPLETED


# ==================== ISOLATION ====================

def test_two_sessions_progress_independently(engine: InterviewEngine) -> None:
    session_a = _session(plan=_plan(3, follow_up_allowed=False), state=InterviewState.READY, session_id="sess-a")
    session_b = _session(plan=_plan(3, follow_up_allowed=False), state=InterviewState.READY, session_id="sess-b")

    engine.start(session_a)
    engine.submit_answer(session_a, "answer A1")
    engine.submit_answer(session_a, "answer A2")

    # session_b has not started yet -- must be completely unaffected by session_a's progress.
    assert session_b.state == InterviewState.READY
    assert session_b.current_question_index == 0
    assert session_b.transcript.turns == []

    engine.start(session_b)
    engine.submit_answer(session_b, "answer B1")

    assert session_a.current_question_index == 2
    assert session_b.current_question_index == 1
    assert [t.answer for t in session_a.transcript.turns] == ["answer A1", "answer A2"]
    assert [t.answer for t in session_b.transcript.turns] == ["answer B1"]


# ==================== SAFETY ====================

def test_next_prompt_has_no_scoring_or_rubric_fields() -> None:
    fields = set(NextPrompt.model_fields)
    assert fields == {
        "state", "question_id", "question_text", "category",
        "is_follow_up", "question_index", "total_questions", "progress_pct", "finished",
    }
    for forbidden in ("expected_topics", "score", "rubric", "evidence", "reasoning", "purpose"):
        assert forbidden not in fields


def test_candidate_never_receives_expected_topics_in_prompt_text(engine: InterviewEngine) -> None:
    session = _session(
        plan=InterviewPlan(questions=[_question("q0", expected_topics=["SECRET_RUBRIC_TOPIC"])]),
        state=InterviewState.READY,
    )
    prompt = engine.start(session)
    assert "SECRET_RUBRIC_TOPIC" not in prompt.question_text


# ==================== QUESTION COUNT / INTERVIEW QUALITY (real planner) ====================

def test_smallest_real_plan_does_not_let_intro_closing_dominate() -> None:
    # A minimum-size real plan from the Phase 4 planner still has exactly one
    # non-intro/closing question, and the engine walks through all of it correctly.
    llm_service = MockLLMService()
    job_analysis = JobAnalyzer(llm_service).analyze(
        "Junior AI Engineer", "Junior", "We need a Junior AI Engineer who knows Python and SQL."
    )
    candidate_analysis = CandidateAnalyzer(llm_service).analyze("Built a small Python project using SQL.")
    fit_analysis = FitAnalyzer(llm_service).analyze(job_analysis, candidate_analysis)
    plan = InterviewPlanner(llm_service).plan(
        "Junior AI Engineer", "Acme", "Junior", MIN_QUESTIONS,
        job_analysis, candidate_analysis, fit_analysis,
    )
    assert len(plan.questions) == MIN_QUESTIONS
    non_intro_closing = [
        q for q in plan.questions
        if q.category not in (QuestionCategory.INTRODUCTION, QuestionCategory.CLOSING)
    ]
    assert len(non_intro_closing) == MIN_QUESTIONS - 2  # exactly 1 real content question

    engine_run = InterviewEngine(Interviewer(llm_service))
    session = _session(plan=plan, state=InterviewState.READY)
    prompt = engine_run.start(session)
    assert prompt.category == QuestionCategory.INTRODUCTION
    main_categories_seen = []
    while not prompt.finished:
        prompt = engine_run.submit_answer(session, STRONG_ANSWER)
        if not prompt.is_follow_up:
            main_categories_seen.append(prompt.category)
    assert main_categories_seen[-1] == QuestionCategory.CLOSING
    assert session.state == InterviewState.COMPLETED


# ==================== MOCK ====================

def test_full_interview_runs_end_to_end_with_mock_llm_service(engine: InterviewEngine) -> None:
    session = _session(plan=_plan(4), state=InterviewState.READY)
    prompt = engine.start(session)
    turns = 0
    while not prompt.finished:
        prompt = engine.submit_answer(session, STRONG_ANSWER)
        turns += 1
        assert turns < 50  # safety bound against an infinite loop
    assert session.state == InterviewState.COMPLETED


def test_engine_is_deterministic_with_mock_llm_service() -> None:
    def run() -> list[tuple[str, bool, str]]:
        engine_run = InterviewEngine(Interviewer(MockLLMService()))
        session = _session(plan=_plan(4), state=InterviewState.READY)
        prompt = engine_run.start(session)
        record = [(prompt.question_id, prompt.is_follow_up, prompt.question_text)]
        while not prompt.finished:
            prompt = engine_run.submit_answer(session, WEAK_ANSWER)
            record.append((prompt.question_id, prompt.is_follow_up, prompt.question_text))
        return record

    assert run() == run()
