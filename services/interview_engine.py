"""InterviewEngine: deterministic interview state machine.

Makes no LLM calls itself -- it asks the injected Interviewer agent whether a
follow-up would be useful, but the engine alone owns and enforces every
counter, cap, and state transition. The Interviewer only ever proposes.

State flow (Phase 5 implements through COMPLETED; EVALUATED is Phase 6):

    CREATED -> READY -> IN_PROGRESS -> COMPLETED -> EVALUATED

The engine holds no session-specific state of its own -- everything it reads
or mutates lives on the InterviewSession object passed into each call, so one
InterviewEngine instance can safely drive many concurrent sessions.
"""
import math
from datetime import datetime, timezone

from agents.interviewer import Interviewer
from models.common import InterviewState
from models.interview import InterviewQuestion, InterviewTranscript, InterviewTurn, NextPrompt
from models.session import InterviewSession
from models.voice_intent import NO_FOLLOW_UP_INTENTS, VoiceIntent
from services.logging_service import Event, content_metadata, log_event

MAX_FOLLOW_UPS_PER_QUESTION = 2
GLOBAL_FOLLOW_UP_BUDGET_RATIO = 0.5

_REFUSAL_MARKERS = (
    "don't know", "do not know", "dont know",
    "don't remember", "do not remember", "dont remember",
    "don't recall", "do not recall", "dont recall",
    "can't remember", "cannot remember", "can't recall", "cannot recall",
    "not sure", "no idea", "n/a",
    "no experience with that", "not familiar with that",
    "prefer not to answer", "skip this",
)

# Refusals often carry a short lead-in ("honestly, ...", "um, ..."), especially when
# spoken. Stripping it keeps a genuine refusal recognisable without inflating its length.
_LEADING_FILLERS = (
    "honestly", "sorry", "um", "uh", "erm", "well", "hmm", "yeah",
    "i mean", "to be honest",
)

# A hedge followed by real content ("not sure exactly, but I did X") is a substantive
# answer, not a refusal. These pivots mark where the substance begins.
_CONTRAST_PIVOTS = (
    "but", "although", "though", "however",
    "what i can say", "what i do remember", "what i remember",
)

# A genuine refusal is a short utterance that is essentially nothing but the refusal.
_MAX_REFUSAL_WORDS = 6
_MIN_SUBSTANTIVE_WORDS_AFTER_PIVOT = 3


class InvalidInterviewStateError(Exception):
    """Raised when an interview operation is attempted from an invalid state."""


class InterviewEngine:
    def __init__(self, interviewer: Interviewer, max_follow_ups_per_question: int = MAX_FOLLOW_UPS_PER_QUESTION) -> None:
        self._interviewer = interviewer
        self._max_follow_ups_per_question = max_follow_ups_per_question

    def start(self, session: InterviewSession) -> NextPrompt:
        if session.state != InterviewState.READY:
            raise InvalidInterviewStateError(
                f"Cannot start an interview from state {session.state}; must be READY."
            )
        if session.interview_plan is None or not session.interview_plan.questions:
            raise InvalidInterviewStateError("Cannot start an interview with no interview plan.")

        session.state = InterviewState.IN_PROGRESS
        session.current_question_index = 0
        session.current_follow_up_count = 0
        session.total_follow_up_count = 0
        question = session.interview_plan.questions[0]
        session.pending_question_text = question.question
        session.updated_at = _now_iso()

        log_event(
            Event.INTERVIEW_STARTED,
            session_id=session.id,
            total_questions=len(session.interview_plan.questions),
        )

        return self._build_next_prompt(session, question, is_follow_up=False, finished=False)

    def current_prompt(self, session: InterviewSession) -> NextPrompt:
        """Return the prompt already owned by an in-progress session.

        Voice can repeat or present a question differently without submitting a
        fake answer or advancing the interview. The engine remains the one place
        that derives the current question and follow-up state.
        """
        if session.state != InterviewState.IN_PROGRESS:
            raise InvalidInterviewStateError(
                f"Cannot read a current prompt from state {session.state}; must be IN_PROGRESS."
            )
        if session.interview_plan is None or not session.interview_plan.questions:
            raise InvalidInterviewStateError("Session has no interview plan.")
        question = session.interview_plan.questions[session.current_question_index]
        return self._build_next_prompt(
            session,
            question,
            is_follow_up=session.current_follow_up_count > 0,
            finished=False,
        )

    def submit_answer(
        self,
        session: InterviewSession,
        answer: str,
        *,
        intent_hint: VoiceIntent | None = None,
    ) -> NextPrompt:
        """``intent_hint`` is an optional, advisory semantic classification of the
        answer (voice callers only -- text/API/null-transport callers pass none
        and see identical behaviour to before this parameter existed). It never
        mutates state directly: it is consulted by ``_maybe_ask_follow_up``
        alongside the pre-existing regex gate, exactly as another proposer's
        input, never as a second source of authority over the engine.
        """
        if session.state != InterviewState.IN_PROGRESS:
            raise InvalidInterviewStateError(
                f"Cannot submit an answer from state {session.state}; must be IN_PROGRESS."
            )
        if session.interview_plan is None:
            raise InvalidInterviewStateError("Session has no interview plan.")

        questions = session.interview_plan.questions
        current_question = questions[session.current_question_index]
        is_follow_up = session.current_follow_up_count > 0
        question_text = session.pending_question_text or current_question.question

        turn = InterviewTurn(
            question_id=current_question.id,
            question=question_text,
            category=current_question.category,
            answer=answer,
            is_follow_up=is_follow_up,
            timestamp=_now_iso(),
        )
        session.transcript.turns.append(turn)
        session.updated_at = turn.timestamp

        log_event(
            Event.QUESTION_ANSWERED,
            session_id=session.id,
            question_id=current_question.id,
            is_follow_up=is_follow_up,
            **content_metadata("answer", answer),
        )

        turns_for_question = session.transcript.turns_for_question(current_question.id)
        follow_up_text = self._maybe_ask_follow_up(
            session, current_question, turns_for_question, answer, intent_hint=intent_hint,
        )

        if follow_up_text is not None:
            session.current_follow_up_count += 1
            session.total_follow_up_count += 1
            session.pending_question_text = follow_up_text
            session.updated_at = _now_iso()
            log_event(
                Event.FOLLOW_UP_CREATED,
                session_id=session.id,
                question_id=current_question.id,
                follow_up_count_for_question=session.current_follow_up_count,
                total_follow_up_count=session.total_follow_up_count,
            )
            return self._build_next_prompt(session, current_question, is_follow_up=True, finished=False)

        return self._advance_to_next_question(session)

    def end(self, session: InterviewSession) -> InterviewTranscript:
        if session.state != InterviewState.IN_PROGRESS:
            raise InvalidInterviewStateError(
                f"Cannot end an interview from state {session.state}; must be IN_PROGRESS."
            )
        session.state = InterviewState.COMPLETED
        session.pending_question_text = None
        session.updated_at = _now_iso()
        log_event(
            Event.INTERVIEW_COMPLETED,
            session_id=session.id,
            total_turns=len(session.transcript.turns),
            total_follow_ups=session.total_follow_up_count,
            natural_completion=False,
        )
        return session.transcript

    # ---- internal ----

    def _maybe_ask_follow_up(
        self,
        session: InterviewSession,
        question: InterviewQuestion,
        turns_for_question: list[InterviewTurn],
        latest_answer: str,
        *,
        intent_hint: VoiceIntent | None = None,
    ) -> str | None:
        if not question.follow_up_allowed:
            return None
        if _is_blank(latest_answer):
            return None
        # The classifier hint is consulted alongside the regex gate, never instead
        # of it: a text/API caller that never supplies a hint (intent_hint=None)
        # sees exactly the pre-existing regex-only behaviour. A voice caller whose
        # classifier confidently recognises a paraphrased "don't know"/refusal that
        # the regex alone would miss gets the same suppression the regex already
        # gives an exact-phrase match -- same rule, wider recall, not a new one.
        if intent_hint in NO_FOLLOW_UP_INTENTS:
            return None
        if _looks_like_dont_know(latest_answer):
            return None
        if session.current_follow_up_count >= self._max_follow_ups_per_question:
            return None
        if session.total_follow_up_count >= _global_follow_up_budget(session):
            return None

        turns_so_far = turns_for_question[:-1]  # exclude the turn just recorded
        decision = self._interviewer.propose_follow_up(question, turns_so_far, latest_answer)
        if not decision.should_ask:
            return None
        return decision.follow_up_question

    def _advance_to_next_question(self, session: InterviewSession) -> NextPrompt:
        questions = session.interview_plan.questions
        next_index = session.current_question_index + 1

        if next_index >= len(questions):
            session.state = InterviewState.COMPLETED
            session.pending_question_text = None
            session.updated_at = _now_iso()
            log_event(
                Event.INTERVIEW_COMPLETED,
                session_id=session.id,
                total_turns=len(session.transcript.turns),
                total_follow_ups=session.total_follow_up_count,
                natural_completion=True,
            )
            last_question = questions[session.current_question_index]
            return self._build_next_prompt(session, last_question, is_follow_up=False, finished=True)

        session.current_question_index = next_index
        session.current_follow_up_count = 0
        next_question = questions[next_index]
        session.pending_question_text = next_question.question
        session.updated_at = _now_iso()
        return self._build_next_prompt(session, next_question, is_follow_up=False, finished=False)

    def _build_next_prompt(
        self, session: InterviewSession, question: InterviewQuestion, is_follow_up: bool, finished: bool,
    ) -> NextPrompt:
        total_questions = len(session.interview_plan.questions)
        question_text = session.pending_question_text or question.question
        progress_pct = _compute_progress_pct(session.current_question_index, total_questions, finished)
        return NextPrompt(
            state=session.state,
            question_id=question.id,
            question_text=question_text,
            category=question.category,
            is_follow_up=is_follow_up,
            question_index=session.current_question_index,
            total_questions=total_questions,
            progress_pct=progress_pct,
            finished=finished,
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_blank(answer: str) -> bool:
    return not answer or not answer.strip()


def _looks_like_dont_know(answer: str) -> bool:
    """Whether an answer is a genuine refusal rather than a substantive response.

    Follow-ups are suppressed only when the answer is effectively nothing but a
    refusal. An answer that hedges and then answers -- "I'm not sure exactly how
    many, but we indexed around 2 million" -- is substantive and stays eligible
    for a follow-up, because the hedge qualifies the detail rather than replacing it.

    This gate is a blunt pre-filter in front of the Interviewer agent, so it is
    deliberately biased toward consulting the agent: wrongly consulting it costs
    one declined proposal, while wrongly suppressing loses real interview signal.
    """
    normalized = _normalize_answer(answer)
    if not normalized:
        return True
    if not any(marker in normalized for marker in _REFUSAL_MARKERS):
        return False
    if _has_substance_after_pivot(normalized):
        return False
    return len(normalized.split()) <= _MAX_REFUSAL_WORDS


def _normalize_answer(answer: str) -> str:
    """Lowercase, collapse whitespace, drop edge punctuation, and strip lead-in fillers."""
    normalized = " ".join(answer.strip().casefold().split()).strip(" .!?,;:")
    stripped_filler = True
    while stripped_filler and normalized:
        stripped_filler = False
        for filler in _LEADING_FILLERS:
            if normalized == filler:
                return ""
            if normalized.startswith(f"{filler} ") or normalized.startswith(f"{filler},"):
                normalized = normalized[len(filler):].lstrip(" ,").strip()
                stripped_filler = True
                break
    return normalized


def _has_substance_after_pivot(normalized: str) -> bool:
    """Whether a contrast pivot is followed by enough words to count as a real answer."""
    for pivot in _CONTRAST_PIVOTS:
        marker = f" {pivot} "
        index = normalized.find(marker)
        if index == -1:
            continue
        remainder = normalized[index + len(marker):]
        if len(remainder.split()) >= _MIN_SUBSTANTIVE_WORDS_AFTER_PIVOT:
            return True
    return False


def _global_follow_up_budget(session: InterviewSession) -> int:
    total_questions = len(session.interview_plan.questions)
    return math.ceil(GLOBAL_FOLLOW_UP_BUDGET_RATIO * total_questions)


def _compute_progress_pct(question_index: int, total_questions: int, finished: bool) -> float:
    """Fraction of MAIN questions fully answered so far, as a percentage.

    question_index only advances when the engine moves to a new main question,
    so this value does not change across a question's follow-ups (never moves
    backward mid-question) and only ever reaches 100 via the explicit
    `finished` flag at natural or early completion -- never by arithmetic
    coincidence from an out-of-range index.
    """
    if finished:
        return 100.0
    if total_questions <= 0:
        return 0.0
    pct = (question_index / total_questions) * 100.0
    return max(0.0, min(100.0, pct))
