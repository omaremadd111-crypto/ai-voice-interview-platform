"""Candidate-facing conversation adapter for voice delivery.

Delivery behaviours live here; interview sequencing, follow-up decisions, limits,
transcript state, evaluation, and scoring remain exclusively in the existing
InterviewAgentService/InterviewEngine path.
"""
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from pydantic import ValidationError

from agents.voice_intent_classifier import VoiceIntentClassificationError, VoiceIntentClassifier
from application.interview_agent_service import InterviewAgentService
from models.agent_persona import VoiceAgentPersona, default_persona
from models.common import InterviewState, QuestionCategory
from models.interview import NextPrompt
from models.voice_intent import (
    NO_FOLLOW_UP_INTENTS,
    VoiceIntent,
    VoiceIntentResult,
)
from services.db.agent_configs import AgentConfigRepository
from services.db.candidates import CandidateRepository
from services.db.positions import PositionRepository
from services.interview_engine import InvalidInterviewStateError
from services.logging_service import Event, content_metadata, log_event


_StoreCallResult = TypeVar("_StoreCallResult")


class VoiceConversationError(Exception):
    """Raised when room metadata does not match persisted candidate data."""


@dataclass(frozen=True)
class VoiceConversationContext:
    session_id: str
    candidate_id: int
    position_id: int
    candidate_name: str
    company_name: str
    position_title: str
    persona: VoiceAgentPersona


@dataclass(frozen=True)
class VoiceReply:
    segments: tuple[str, ...]
    finished: bool = False
    #: Delivery metadata only: the engine-owned question this reply belongs to,
    #: so the LiveKit layer can tell the silence coordinator when a new question
    #: has been asked. Never persisted, never read by the evaluator.
    question_id: str | None = None
    #: True only when the engine has ALREADY advanced/committed to a new
    #: question by the time these segments were computed (this reply followed
    #: a real start_interview/submit_answer call) -- the LAST segment is then
    #: that question, and it must always be delivered in full: interrupting an
    #: earlier segment (reaction/transition) must not skip it, and it must not
    #: itself be interruptible (P0-2 / plan invariant I7 extended to delivery).
    #: Meta-intent replies that merely re-speak the CURRENT, unchanged question
    #: (repeat/off-topic/candidate-question/etc.) leave this False -- dropping
    #: those on interruption is harmless because no engine state moved, so
    #: their behaviour is byte-identical to before P0-2.
    question_must_be_delivered: bool = False


class VoiceConversationService:
    """Adds persona phrasing around, but never instead of, the interview core."""

    def __init__(
        self,
        agent_service: InterviewAgentService,
        candidate_repo: CandidateRepository,
        position_repo: PositionRepository,
        agent_config_repo: AgentConfigRepository,
        intent_classifier: VoiceIntentClassifier | None = None,
        *,
        intent_min_confidence: float = 0.55,
        max_repeats_per_question: int = 2,
    ) -> None:
        self._agent_service = agent_service
        self._candidate_repo = candidate_repo
        self._position_repo = position_repo
        self._agent_config_repo = agent_config_repo
        # None is the semantic-layer kill switch: a caller that does not construct
        # a classifier (or that has VOICE_SEMANTIC_LAYER_ENABLED=false) gets
        # exactly the pre-Phase-1 regex-only don't-know/refusal behaviour, with
        # zero classifier calls.
        self._intent_classifier = intent_classifier
        self._intent_min_confidence = intent_min_confidence
        self._max_repeats_per_question = max_repeats_per_question
        if max_repeats_per_question < 1:
            raise ValueError("max_repeats_per_question must be at least 1")
        # A LiveKit endpoint can split a false start from its continuation. Keep
        # that delivery-only fragment out of the authoritative interview transcript
        # until the candidate completes the thought.
        self._pending_utterances: dict[str, str] = {}
        self._pause_acknowledged: set[str] = set()
        self._answer_counts: dict[str, int] = {}
        self._last_acknowledgements: dict[str, str] = {}
        # (session_id, question_id) -> repeat/rephrase requests seen for the
        # current question. Delivery-only; drives the offer-to-move-on after N.
        self._repeat_counts: dict[tuple[str, str], int] = {}
        # session_id -> {"classify_ms": ..., "engine_ms": ...}, populated during
        # the current respond() call and retrieved by the LiveKit layer via
        # pop_turn_timings() to assemble one VOICE_TURN_LATENCY event per turn
        # (P0-4). Pure instrumentation: never read by any conversation decision.
        self._pending_turn_timings: dict[str, dict[str, float | None]] = {}

    def load_context(
        self, *, session_id: str, candidate_id: int, position_id: int,
    ) -> VoiceConversationContext:
        candidate = self._candidate_repo.get(candidate_id)
        position = self._position_repo.get(position_id)
        if candidate.position_id != position_id:
            raise VoiceConversationError("Candidate and position metadata do not match.")
        persona = resolve_voice_persona(
            self._agent_config_repo, position.owner_id, position_id, position.company_name,
        )
        return VoiceConversationContext(
            session_id=session_id,
            candidate_id=candidate_id,
            position_id=position_id,
            candidate_name=candidate.full_name,
            company_name=position.company_name,
            position_title=position.title,
            persona=persona,
        )

    def begin(self, context: VoiceConversationContext) -> VoiceReply:
        self._reset_delivery_state(context.session_id)
        prompt = self._agent_service.start_interview(context.session_id)
        return VoiceReply(
            self._with_pause(
                context,
                self._render(context.persona.opening_script, context),
                self._question(prompt, context),
            ),
            question_id=prompt.question_id,
            # start_interview already committed the engine to question 1 before
            # any audio plays -- same orphaning risk as a mid-interview
            # submit_answer (P0-2): a barge-in during the opening must not skip
            # the candidate's first actual question.
            question_must_be_delivered=True,
        )

    def restate_question(self, context: VoiceConversationContext) -> VoiceReply:
        """Presence-retry delivery: speak the current question again, unchanged.

        The approved plan's question is re-rendered verbatim; nothing is
        submitted and no transcript turn is created. The silence coordinator
        uses this when a candidate has gone uncharacteristically quiet.
        """
        prompt = self._agent_service.get_current_prompt(context.session_id)
        return VoiceReply(
            self._with_pause(context, self._question(prompt, context)),
            question_id=prompt.question_id,
        )

    def resolve_no_response(
        self,
        context: VoiceConversationContext,
        *,
        nudge_count: int,
        elapsed_seconds: float,
    ) -> VoiceReply:
        """Silence-ladder exhaustion for the current question (Phase 2).

        Moves the interview on WITHOUT fabricating candidate speech: when the
        candidate said nothing at all, an empty string is submitted through the
        normal engine path -- the tested blank-answer behaviour where no
        follow-up is proposed and the turn is recorded with answer="" -- so the
        transcript honestly contains no candidate words. Race R5: if a buffered
        fragment of real speech is pending, those words are submitted instead of
        being silently discarded. Synthetic placeholders like "[no response]"
        are forbidden (plan section 10 / invariant I7).
        """
        session_id = context.session_id
        buffered = self._pending_utterances.pop(session_id, None)
        answer_text = (buffered or "").strip()
        self._pause_acknowledged.discard(session_id)

        question_id: str | None = None
        try:
            question_id = self._agent_service.get_current_prompt(session_id).question_id
        except InvalidInterviewStateError:
            # The interview ended while the ladder was ticking; there is no
            # question left to resolve and nothing may be submitted.
            return VoiceReply(())

        log_event(
            Event.VOICE_NO_RESPONSE,
            session_id=session_id,
            question_id=question_id,
            nudge_count=nudge_count,
            elapsed_seconds=round(elapsed_seconds),
            had_pending_buffer=bool(buffered),
            **content_metadata("buffered_utterance", answer_text),
        )

        prompt = self._agent_service.submit_answer(session_id, answer_text)
        if prompt.finished or prompt.state is InterviewState.COMPLETED:
            return VoiceReply(
                (self._render(context.persona.closing_script, context),),
                finished=True,
            )

        segments: list[str] = []
        if not buffered:
            segments.append(self._next_move_on_reaction(session_id))
        else:
            acknowledgement = self._acknowledgement(context, answer_text, prompt)
            if acknowledgement:
                segments.append(acknowledgement)
        segments.append(self._question(prompt, context))
        return VoiceReply(
            self._with_pause(context, *segments),
            question_id=prompt.question_id,
            # submit_answer above already advanced the engine past the silent
            # question; the ladder's own nudge/reaction line must not be able
            # to swallow it on a late barge-in (P0-2).
            question_must_be_delivered=True,
        )

    def respond(self, context: VoiceConversationContext, transcript: str) -> VoiceReply:
        # Clear any prior turn's timing bookkeeping unconditionally (P0-4): a
        # caller that skips pop_turn_timings() on some turns must never let a
        # stale classify_ms/engine_ms from an earlier turn leak into this one.
        self._pending_turn_timings.pop(context.session_id, None)

        cleaned = transcript.strip()
        normalized = _normalize_utterance(cleaned)
        persona = context.persona

        # ---- deterministic fast path (plan section 5): 0 ms, $0, never an LLM --
        # Only genuinely high-confidence cases live here; everything else is
        # ambiguous by definition and goes to semantic classification.
        if not cleaned:
            # An empty final transcript is normally endpointing noise, not evidence
            # that the connection failed. Silence is more natural than a mechanical
            # connectivity prompt here.
            return VoiceReply(())
        if self._is_stop_request(normalized):
            self._pending_utterances.pop(context.session_id, None)
            engine_started = time.monotonic()
            self._agent_service.end_interview(context.session_id)
            self._record_timing(
                context.session_id, "engine_ms", (time.monotonic() - engine_started) * 1000,
            )
            return VoiceReply((self._render(persona.closing_script, context),), finished=True)
        if _is_filler_only(normalized):
            return VoiceReply(())

        combined = _join_utterances(
            self._pending_utterances.get(context.session_id), cleaned,
        )
        if _is_incomplete_utterance(combined):
            self._pending_utterances[context.session_id] = combined
            return VoiceReply(())

        self._pending_utterances.pop(context.session_id, None)

        # ---- single structured classification call (Phase 1/3) -----------------
        # Length is never used to infer "substantive" -- a completed, non-filler
        # utterance of any length is ambiguous by default and goes to semantic
        # classification so paraphrased meta-intents and don't-know/refusal
        # answers are recognised regardless of exact wording. One call carries
        # intent + confidence + reaction + transition together (no second LLM
        # round trip for reactions -- plan section 16).
        #
        # current_prompt is fetched here AT MOST ONCE per turn (Batch 2 latency
        # fix): every downstream call site that used to re-fetch the same,
        # still-unchanged prompt (_dispatch_semantic's meta-intent handling,
        # _submit_and_reply's prompt_before) now reuses this value instead.
        # Nothing between this fetch and either reuse point can have moved the
        # engine on -- classify() is a read-only model call, and the only
        # branches that mutate state before this point (STOP_REQUEST's
        # end_interview) return immediately without reaching either reuse site
        # -- so the reused value is always identical to what a fresh fetch
        # would have returned.
        current_prompt: NextPrompt | None = None
        if self._intent_classifier is not None:
            current_prompt = self._timed_store_call(
                context.session_id,
                "prompt_lookup_ms",
                lambda: self._agent_service.get_current_prompt(context.session_id),
            )
        intent_result = self._classify_intent(context, combined, current_prompt)
        if intent_result is not None:
            handled = self._dispatch_semantic(context, intent_result, combined, current_prompt)
            if handled is not None:
                return handled

        # ---- legacy deterministic chain ---------------------------------------
        # Runs when there is no classifier (kill switch), when it failed or timed
        # out, or when its confidence was below threshold -- reproducing exactly
        # the pre-Phase-3 behaviour for those turns (invariant I6).
        if self._is_repeat_request(normalized):
            if persona.conversational_style.allow_question_rephrasing:
                prompt = self._timed_store_call(
                    context.session_id,
                    "prompt_lookup_ms",
                    lambda: self._agent_service.get_current_prompt(context.session_id),
                )
                return VoiceReply(
                    (f"Sure — {self._rephrase(self._question(prompt, context))}",),
                    question_id=prompt.question_id,
                )
            return self._redirect(context, "Sure, I'll repeat it.")
        if self._is_candidate_question(normalized):
            return self._handle_candidate_question(context, normalized)
        if self._is_off_topic(normalized):
            return self._redirect(
                context,
                "Let's keep this focused on your experience and the role.",
            )
        if self._is_pause_request(normalized):
            if context.session_id in self._pause_acknowledged:
                return VoiceReply(())
            self._pause_acknowledged.add(context.session_id)
            return VoiceReply(("Take your time.",))

        return self._submit_and_reply(context, combined, intent_result, current_prompt)

    def _dispatch_semantic(
        self,
        context: VoiceConversationContext,
        result: VoiceIntentResult,
        utterance: str,
        current_prompt: NextPrompt | None,
    ) -> VoiceReply | None:
        """Route a confident classification to its delivery-only handling.

        Meta-intents (hesitation, repeat/rephrase requests, candidate questions,
        off-topic drift, connection checks) NEVER reach submit_answer: they
        create no transcript turn and no interview state changes. Answer intents
        (substantive/partial/dont_know/genuine_refusal) return None so the caller
        submits the literal utterance through the engine path.

        ``current_prompt`` is the SAME prompt already fetched by the caller for
        classification (Batch 2): every branch below that needs the current
        prompt reuses it instead of re-fetching, since nothing between that
        fetch and here can have changed it. Always non-None whenever this
        method runs (the caller only calls it when a classifier result exists,
        which only happens when it already fetched this).
        """
        normalized = _normalize_utterance(utterance)
        persona = context.persona

        if result.intent in (VoiceIntent.DONT_KNOW, VoiceIntent.GENUINE_REFUSAL):
            return None  # submitted with hint; reaction chosen after submission

        if result.intent is VoiceIntent.STOP_REQUEST:
            # Semantic stop recognition for non-exact phrasings; the exact-match
            # fast path above still ends instantly without any model call.
            engine_started = time.monotonic()
            self._agent_service.end_interview(context.session_id)
            self._record_timing(
                context.session_id, "engine_ms", (time.monotonic() - engine_started) * 1000,
            )
            return VoiceReply((self._render(persona.closing_script, context),), finished=True)

        prompt = current_prompt

        if result.intent is VoiceIntent.HESITATION:
            # Same once-per-question courtesy as the deterministic pause path;
            # the candidate is present and thinking, not answering yet.
            if context.session_id in self._pause_acknowledged:
                return VoiceReply(())
            self._pause_acknowledged.add(context.session_id)
            return VoiceReply(("Take your time.",))

        if result.intent is VoiceIntent.INCOMPLETE_UTTERANCE:
            # Semantic refinement of the deterministic buffer: same contract --
            # never submitted as a final answer, never discarded silently.
            self._pending_utterances[context.session_id] = utterance
            return VoiceReply(())

        if result.intent is VoiceIntent.CONNECTION_CHECK:
            return VoiceReply((
                "Yes, I can hear you clearly. Whenever you're ready, please go ahead.",
            ))

        if result.intent is VoiceIntent.REPEAT_REQUEST:
            return self._repeat_or_rephrase_reply(
                context, prompt, result, verbatim=True,
            )
        if result.intent is VoiceIntent.REPHRASE_REQUEST:
            return self._repeat_or_rephrase_reply(
                context, prompt, result, verbatim=False,
            )

        if result.intent is VoiceIntent.CANDIDATE_QUESTION:
            # Detection is semantic; the ANSWER content stays deterministic
            # (verified-info map or honest HR deferral) -- never free-generated
            # about salary, process, or outcomes (plan section 12).
            return self._candidate_question_semantic_reply(context, prompt, normalized, result)

        if result.intent is VoiceIntent.OFF_TOPIC:
            # Brief, non-dismissive redirect back to the current question. No
            # transcript turn: drifted content is not interview evidence, but it
            # is also not lost -- nothing was asked of the candidate here.
            return VoiceReply(
                self._with_pause(
                    context,
                    "That's a bit outside what I can cover here.",
                    self._question(prompt, context),
                ),
                question_id=prompt.question_id,
            )

        # substantive_answer / partial_answer fall through to submission.
        return None

    def _repeat_or_rephrase_reply(
        self,
        context: VoiceConversationContext,
        prompt: NextPrompt,
        result: VoiceIntentResult,
        *,
        verbatim: bool,
    ) -> VoiceReply:
        """Repeat says the approved question as-is; rephrase rewords it.

        Both are delivery transforms on the SAME approved question substance
        (I1): the plan's wording is never replaced by different content. After
        too many repeats the agent offers to move on instead of looping forever.
        """
        session_id = context.session_id
        key = (session_id, prompt.question_id)
        count = self._repeat_counts.get(key, 0) + 1
        self._repeat_counts[key] = count

        spoken_question = self._question(prompt, context)
        if verbatim or not context.persona.conversational_style.allow_question_rephrasing:
            segments = [spoken_question] if verbatim else [f"Sure, here it is again. {spoken_question}"]
        else:
            segments = [f"Sure — {self._rephrase(spoken_question)}"]

        if count > self._max_repeats_per_question:
            segments.insert(
                0,
                "We can skip ahead whenever you're ready — just say so.",
            )
        return VoiceReply(self._with_pause(context, *segments), question_id=prompt.question_id)

    def _candidate_question_semantic_reply(
        self,
        context: VoiceConversationContext,
        prompt: NextPrompt,
        normalized: str,
        result: VoiceIntentResult,
    ) -> VoiceReply:
        answer = self._candidate_question_answer(context, normalized)
        transition = (
            self._peek_generated(result.transition, context)
            if result.transition and prompt.category is not QuestionCategory.CLOSING
            else None
        )
        return_line = transition or "When you're ready, let's come back to the interview."
        if prompt.category is QuestionCategory.CLOSING:
            return VoiceReply(
                self._with_pause(context, answer, self._question(prompt, context)),
                question_id=prompt.question_id,
            )
        return VoiceReply(
            self._with_pause(context, answer, return_line, self._question(prompt, context)),
            question_id=prompt.question_id,
        )

    def _submit_and_reply(
        self,
        context: VoiceConversationContext,
        utterance: str,
        result: VoiceIntentResult | None,
        current_prompt: NextPrompt | None = None,
    ) -> VoiceReply:
        """Submit the LITERAL utterance through the core, then speak the reply.

        The only place answer-shaped voice turns reach InterviewEngine. The
        transcript receives exactly what the candidate said (I7); reaction and
        transition text is spoken delivery only, safety-filtered, and never
        persisted.

        ``current_prompt``, when given (Batch 2), is the prompt the caller
        already fetched for classification -- reused here as ``prompt_before``
        instead of re-fetching, since nothing between that fetch and
        submit_answer below can have changed it. When absent (classifier
        disabled, so nothing was fetched yet this turn) this method still does
        its own fetch, exactly as before.
        """
        # A real submitted answer starts a fresh conversational beat: the
        # once-per-question "Take your time." courtesy becomes available again.
        self._pause_acknowledged.discard(context.session_id)
        prompt_before: NextPrompt | None
        if current_prompt is not None:
            prompt_before = current_prompt
        else:
            try:
                prompt_before = self._timed_store_call(
                    context.session_id,
                    "prompt_lookup_ms",
                    lambda: self._agent_service.get_current_prompt(context.session_id),
                )
            except InvalidInterviewStateError:
                prompt_before = None

        intent_hint = result.intent if result is not None else None
        engine_started = time.monotonic()
        prompt = self._agent_service.submit_answer(
            context.session_id, utterance, intent_hint=intent_hint,
        )
        self._record_timing(
            context.session_id, "engine_ms", (time.monotonic() - engine_started) * 1000,
        )
        if prompt.finished or prompt.state is InterviewState.COMPLETED:
            return VoiceReply(
                (self._render(context.persona.closing_script, context),),
                finished=True,
            )

        segments: list[str] = []
        if result is not None and result.intent in NO_FOLLOW_UP_INTENTS:
            generated = self._use_generated_or(
                result.reaction, context, self._next_move_on_reaction(context.session_id),
            )
            segments.append(generated)
        else:
            # Cadence first (the pre-existing rotation decides WHETHER anything
            # is spoken), then content: a clean classifier reaction outranks the
            # rotation so acknowledgements stay caused by what was said. The
            # dedupe reference is what was actually SPOKEN last turn, snapshotted
            # before the rotation mutates it.
            previously_spoken = self._last_acknowledgements.get(context.session_id)
            acknowledgement = self._acknowledgement(context, utterance, prompt)
            if acknowledgement:
                usable = (
                    self._peek_generated(
                        result.reaction, context, previously_spoken=previously_spoken,
                    )
                    if result
                    else None
                )
                if usable is not None:
                    self._commit_generated(usable, context)
                    segments.append(usable)
                else:
                    segments.append(acknowledgement)

        category_changed = (
            prompt_before is not None
            and prompt.category is not prompt_before.category
            and prompt.category is not QuestionCategory.CLOSING
        )
        if category_changed:
            transition = (
                self._peek_generated(result.transition, context)
                if result is not None and result.transition
                else None
            ) or _CATEGORY_TRANSITION_LINES.get(prompt.category)
            if transition:
                segments.append(transition)

        segments.append(self._question(prompt, context))
        return VoiceReply(
            self._with_pause(context, *segments),
            question_id=prompt.question_id,
            # submit_answer above already advanced the engine to this question
            # (or to a follow-up); a barge-in during the reaction/transition
            # must not skip it (P0-2).
            question_must_be_delivered=True,
        )

    def _classify_intent(
        self,
        context: VoiceConversationContext,
        utterance: str,
        current_prompt: NextPrompt | None,
    ) -> VoiceIntentResult | None:
        """Advisory only: returns None on kill switch, classifier failure/timeout,
        or below-confidence output, in which case the caller passes no hint and
        the interview behaves exactly as it did before Phase 1 (the engine's own
        regex gate is still the fallback safety net for exact-phrase refusals).

        ``current_prompt`` is fetched by the caller (Batch 2), not here -- the
        caller needs it before deciding whether to call this method at all
        (it is None precisely when the kill switch is off), so fetching it a
        second time in here would just be the redundant call this phase
        removes.
        """
        if self._intent_classifier is None:
            # Kill switch off: the classifier was never invoked at all, so no
            # classify_ms is recorded for pop_turn_timings (None means "not
            # attempted", distinct from "attempted in ~0ms").
            return None
        question_text = (current_prompt.question_text or "") if current_prompt is not None else ""
        upcoming_category = self._upcoming_category_label(context)
        started = time.monotonic()
        try:
            result = self._intent_classifier.classify(
                question_text, utterance, upcoming_category=upcoming_category,
            )
        except VoiceIntentClassificationError:
            elapsed_ms = (time.monotonic() - started) * 1000
            log_event(
                Event.VOICE_INTENT_FALLBACK,
                session_id=context.session_id,
                latency_ms=round(elapsed_ms),
            )
            # The attempt still cost real wall-clock time even though it
            # failed/timed out -- that latency is real turn latency the
            # candidate experienced, so it is recorded regardless (P0-4).
            self._record_timing(context.session_id, "classify_ms", elapsed_ms)
            return None
        elapsed_ms = (time.monotonic() - started) * 1000
        log_event(
            Event.VOICE_INTENT_CLASSIFIED,
            session_id=context.session_id,
            intent=result.intent.value,
            confidence=round(result.confidence, 3),
            latency_ms=round(elapsed_ms),
        )
        self._record_timing(context.session_id, "classify_ms", elapsed_ms)
        if result.confidence < self._intent_min_confidence:
            return None
        return result

    def _upcoming_category_label(self, context: VoiceConversationContext) -> str | None:
        """Engine-owned look-ahead fed to the classifier's user prompt."""
        getter = getattr(self._agent_service, "get_upcoming_category", None)
        if getter is None:
            return None
        try:
            category = self._timed_store_call(
                context.session_id, "category_lookup_ms", lambda: getter(context.session_id),
            )
        except Exception:  # noqa: BLE001 -- advisory metadata must never break a turn
            return None
        return category.value if category is not None else None

    def _dont_know_reaction(
        self, context: VoiceConversationContext, intent_result: VoiceIntentResult,
    ) -> str:
        return self._use_generated_or(
            intent_result.reaction,
            context,
            self._next_move_on_reaction(context.session_id),
        )

    def _next_move_on_reaction(self, session_id: str) -> str:
        """Deterministic "let's move on" line, never repeating back-to-back.

        Fixed strings only -- never LLM-generated at these call sites -- so
        every fallback path stays free-text-safety reviewed by construction.
        """
        last = self._last_acknowledgements.get(session_id)
        reaction = next(
            (candidate for candidate in _DONT_KNOW_REACTIONS if candidate != last),
            _DONT_KNOW_REACTIONS[0],
        )
        self._last_acknowledgements[session_id] = reaction
        return reaction

    def _peek_generated(
        self,
        text: str | None,
        context: VoiceConversationContext,
        *,
        previously_spoken: str | None = None,
    ) -> str | None:
        """Safety gate on classifier-generated spoken text (plan section 15).

        A reaction/transition is free text from an LLM that gets SPOKEN to a
        candidate. It must never comment on competence, performance, or likely
        outcome; never reference emotion, accent, personality, or any protected
        attribute; and never imply any hiring decision. Anything matching the
        banned vocabulary is dropped -- the caller falls back to its
        pre-reviewed deterministic line. The raw text is never logged.

        Returns the usable line WITHOUT committing it as "last spoken"; callers
        that actually speak it must call ``_commit_generated``.
        """
        if not text:
            return None
        if _GENERATED_TEXT_BANNED_PATTERN.search(text):
            log_event(
                Event.VOICE_REACTION_FILTERED,
                session_id=context.session_id,
                **content_metadata("filtered_text", text),
            )
            return None
        # Never repeat the same generated line back-to-back: variety is part of
        # the fix ("repetition is the defect", plan section 15). Callers that
        # run the rotation first pass the PRE-turn spoken line here, because the
        # rotation overwrites the "last spoken" slot with its own pick.
        reference = (
            previously_spoken
            if previously_spoken is not None
            else self._last_acknowledgements.get(context.session_id)
        )
        if text == reference:
            return None
        return text

    def _commit_generated(self, text: str, context: VoiceConversationContext) -> None:
        self._last_acknowledgements[context.session_id] = text

    def _use_generated_or(
        self,
        text: str | None,
        context: VoiceConversationContext,
        fallback: str,
    ) -> str:
        """Peek a generated line; use it if clean, otherwise the fallback."""
        usable = self._peek_generated(text, context)
        if usable is not None:
            self._commit_generated(usable, context)
            return usable
        return fallback

    def _redirect(self, context: VoiceConversationContext, acknowledgement: str) -> VoiceReply:
        prompt = self._timed_store_call(
            context.session_id,
            "prompt_lookup_ms",
            lambda: self._agent_service.get_current_prompt(context.session_id),
        )
        return VoiceReply(
            self._with_pause(context, acknowledgement, self._question(prompt, context)),
            question_id=prompt.question_id,
        )

    def _handle_candidate_question(
        self, context: VoiceConversationContext, normalized: str,
    ) -> VoiceReply:
        if _is_request_to_ask(normalized):
            return VoiceReply(("Of course. What would you like to know?",))

        prompt = self._timed_store_call(
            context.session_id,
            "prompt_lookup_ms",
            lambda: self._agent_service.get_current_prompt(context.session_id),
        )
        answer = self._candidate_question_answer(context, normalized)
        if prompt.category is QuestionCategory.CLOSING:
            return VoiceReply(
                self._with_pause(context, answer, self._question(prompt, context)),
                question_id=prompt.question_id,
            )
        return VoiceReply(
            self._with_pause(
                context,
                answer,
                "When you're ready, let's come back to the interview.",
                self._question(prompt, context),
            ),
            question_id=prompt.question_id,
        )

    @staticmethod
    def _candidate_question_answer(
        context: VoiceConversationContext, normalized: str,
    ) -> str:
        if any(
            term in normalized
            for term in (
                "next step", "outcome", "decision", "interview process", "hiring process",
            )
        ):
            return (
                "A human recruiter reviews the screening and will contact you about next steps."
            )
        topic = "that"
        if "salary" in normalized or "pay" in normalized:
            topic = "compensation"
        elif "benefit" in normalized:
            topic = "benefits"
        elif "team" in normalized:
            topic = "the team"
        elif "culture" in normalized:
            topic = "company culture"
        elif any(term in normalized for term in ("working hours", "schedule")):
            topic = "working hours"
        elif any(term in normalized for term in ("office", "location", "remote", "hybrid")):
            topic = "the work location"
        elif any(term in normalized for term in ("role", "position", "job title")):
            return (
                f"I can confirm this interview is for the {context.position_title} role at "
                f"{context.company_name}. HR can provide any details beyond the approved posting."
            )
        return (
            f"I don't have verified information about {topic} in this interview context, "
            "so I don't want to guess. HR can give you the accurate details."
        )

    @staticmethod
    def _render(script: str, context: VoiceConversationContext) -> str:
        candidate = context.candidate_name
        if not context.persona.conversational_style.use_candidate_name:
            candidate = "there"
        first = candidate.split()[0] if candidate.split() else "there"
        replacements = {
            "{candidate_name}": candidate,
            "{candidate_first_name}": first,
            "{agent_name}": context.persona.agent_name,
            "{company_name}": context.persona.company_name,
        }
        rendered = script
        for placeholder, value in replacements.items():
            rendered = rendered.replace(placeholder, value)
        return rendered

    @staticmethod
    def _question(prompt: NextPrompt, context: VoiceConversationContext) -> str:
        if not prompt.question_text:
            raise VoiceConversationError("The interview core returned no current question.")
        if prompt.category is QuestionCategory.CLOSING:
            return (
                "Before we wrap up, what questions do you have about the role, "
                f"the team, or {context.company_name}?"
            )
        return _shorten_spoken_question(prompt.question_text)

    @staticmethod
    def _with_pause(context: VoiceConversationContext, *segments: str) -> tuple[str, ...]:
        if context.persona.conversational_style.natural_pauses:
            return tuple(segment for segment in segments if segment)
        return (" ".join(segment for segment in segments if segment),)

    def _acknowledgement(
        self,
        context: VoiceConversationContext,
        answer: str,
        next_prompt: NextPrompt,
    ) -> str | None:
        if not context.persona.conversational_style.brief_acknowledgements:
            return None

        session_id = context.session_id
        count = self._answer_counts.get(session_id, 0) + 1
        self._answer_counts[session_id] = count

        # A follow-up already provides the conversational response to the answer.
        # Before the closing invitation, moving directly there sounds more natural.
        if (
            count % 2 == 0
            or next_prompt.is_follow_up
            or next_prompt.category is QuestionCategory.CLOSING
        ):
            return None

        if _looks_like_concrete_example(answer):
            options = ("Good example.", "That makes sense.", "Got it.")
        else:
            options = ("Got it.", "Understood.", "That makes sense.")
        acknowledgement = options[((count - 1) // 2) % len(options)]

        if acknowledgement == self._last_acknowledgements.get(session_id):
            acknowledgement = options[(options.index(acknowledgement) + 1) % len(options)]
        self._last_acknowledgements[session_id] = acknowledgement
        return acknowledgement

    @staticmethod
    def _rephrase(question: str) -> str:
        stripped = question.strip()
        lowered = stripped.casefold()
        if lowered.startswith("tell me about"):
            return f"could you walk me through {stripped[13:].lstrip()}"
        if lowered.startswith("describe"):
            return f"could you give me an example of {stripped[8:].lstrip()}"
        return f"in your own words, {stripped[0].lower() + stripped[1:] if stripped else stripped}"

    @staticmethod
    def _is_repeat_request(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "repeat the question", "say that again", "rephrase", "what was the question",
                "what do you mean", "can you clarify", "can you explain the question",
                "can you explain that", "can you tell me more",
            )
        )

    @staticmethod
    def _is_pause_request(text: str) -> bool:
        if len(text) > 80:
            return False
        words = text.split()
        while words and words[0].strip(".,!?;:") in _FILLER_WORDS:
            words.pop(0)
        pause = " ".join(words)
        return pause in {
            "give me a moment", "give me a second", "i need a moment", "i need a second",
            "need a moment", "one moment", "one second", "let me think",
            "let me think for a moment", "let's see",
        }

    @staticmethod
    def _is_stop_request(text: str) -> bool:
        return text in {"stop the interview", "end the interview", "i want to stop", "goodbye"}

    @staticmethod
    def _is_candidate_question(text: str) -> bool:
        if _is_request_to_ask(text):
            return True
        if text.startswith(
            (
                "what i ", "what we ", "when i ", "when we ", "where i ", "where we ",
                "how i ", "how we ", "why i ", "why we ",
            )
        ):
            return False
        if not text.startswith(
            (
                "what", "when", "where", "who", "how", "can", "could", "is", "are",
                "will", "i'd like to know", "i would like to know", "tell me about",
            )
        ):
            return False
        return any(
            topic in text
            for topic in (
                "salary", "pay", "benefit", "next step", "process", "working hours",
                "schedule", "office", "location", "remote", "hybrid", "outcome",
                "decision", "role", "position", "job", "team", "company", "culture",
            )
        )

    @staticmethod
    def _is_off_topic(text: str) -> bool:
        return (
            "tell me a joke" in text
            or text.startswith(("what is the weather", "how is the weather", "play music"))
            or "what is your favorite movie" in text
            or "what was the football score" in text
        )

    def _reset_delivery_state(self, session_id: str) -> None:
        self._pending_utterances.pop(session_id, None)
        self._pause_acknowledged.discard(session_id)
        self._answer_counts.pop(session_id, None)
        self._last_acknowledgements.pop(session_id, None)
        self._repeat_counts = {
            key: count for key, count in self._repeat_counts.items()
            if key[0] != session_id
        }
        self._pending_turn_timings.pop(session_id, None)

    def _record_timing(self, session_id: str, key: str, value_ms: float) -> None:
        """Stash one latency stage for pop_turn_timings (P0-4). Instrumentation
        only -- this value is never read by any conversation decision. Use for
        stages that occur at most once per turn (classify_ms, engine_ms); a
        second call with the same key overwrites rather than accumulates."""
        self._pending_turn_timings.setdefault(session_id, {})[key] = round(value_ms, 3)

    def _timed_store_call(
        self, session_id: str, stage: str, call: Callable[[], _StoreCallResult],
    ) -> _StoreCallResult:
        """Run one InterviewAgentService session-store round trip, adding its
        wall time to ``stage``'s running total for this turn (Batch 2).

        Unlike classify_ms/engine_ms (each recorded at most once per turn),
        a session-store stage can legitimately be hit more than once in the
        same turn -- e.g. the one classification-time ``get_current_prompt``
        fetch, plus a legacy-chain fallback fetch on a turn where the
        classifier's result didn't end up used -- so this accumulates onto
        any prior value for ``stage`` rather than overwriting it. Timing is
        recorded even when ``call`` raises (the attempt still cost real
        wall-clock time -- same rationale as classify_ms on a classifier
        failure), then the exception propagates unchanged. Instrumentation
        only -- the returned value is real and used normally by the caller,
        but the timing bookkeeping itself is never read by any conversation
        decision.
        """
        started = time.monotonic()
        try:
            return call()
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            bucket = self._pending_turn_timings.setdefault(session_id, {})
            bucket[stage] = round((bucket.get(stage) or 0.0) + elapsed_ms, 3)

    def pop_turn_timings(self, session_id: str) -> dict[str, float | None]:
        """Retrieve and clear this session's latency stages from the most
        recent respond() call (P0-4; session-store stages added Batch 2).
        Always returns all four keys; a value is None when that stage did not
        occur for this turn -- e.g. classify_ms is None when the
        semantic-layer kill switch is off (the classifier was never invoked
        at all, as opposed to invoked and failing/timing out, which still
        records real elapsed time), engine_ms is None for a meta-intent reply
        (repeat/off-topic/candidate-question/etc.) that never called
        submit_answer or end_interview, category_lookup_ms is None whenever
        classify_ms is (the upcoming-category lookup only ever runs as part
        of classification), and prompt_lookup_ms is None only on a fast-path
        turn (empty/stop/filler/incomplete) that never needed the current
        question at all."""
        recorded = self._pending_turn_timings.pop(session_id, {})
        return {
            "classify_ms": recorded.get("classify_ms"),
            "engine_ms": recorded.get("engine_ms"),
            "prompt_lookup_ms": recorded.get("prompt_lookup_ms"),
            "category_lookup_ms": recorded.get("category_lookup_ms"),
        }


#: Fixed, deterministic fallback used whenever the classifier's own optional
#: `reaction` is missing/invalid/filtered -- never LLM-generated at this call
#: site, so there is no free-text-safety review needed for the fallback itself.
_DONT_KNOW_REACTIONS = (
    "No problem, let's move on.",
    "That's okay — let's move to the next one.",
    "No worries, let's continue.",
)

#: Deterministic category-change segues used when the classifier did not supply
#: a usable `transition`. Keyed by the NEXT question's category; pre-reviewed
#: constants, so the fallback path needs no runtime safety gate.
_CATEGORY_TRANSITION_LINES: dict[QuestionCategory, str] = {
    QuestionCategory.TECHNICAL: "Let's switch to the technical side.",
    QuestionCategory.PROBLEM_SOLVING: "Next, let's talk through some problem solving.",
    QuestionCategory.BEHAVIORAL: "Let's shift to how you work with others.",
    QuestionCategory.CV_PROJECT_VALIDATION: "Let's dig into your project experience.",
    QuestionCategory.CANDIDATE_BACKGROUND: "Let's talk a bit about your background.",
}

#: Banned-vocabulary screen for ANY classifier-generated spoken text (reaction or
#: transition). Plan section 15 safety constraint: generated lines must never
#: comment on competence/performance/outcome, reference emotion/accent/
#: personality/protected attributes, or imply a hiring decision. Matched on word
#: boundaries against casefolded text; a match drops the line entirely.
_GENERATED_TEXT_BANNED_PATTERN = re.compile(
    r"\b("
    # hiring-decision vocabulary
    r"hire[ds]?|hiring|reject(?:ed|ion|ing|s)?|disqualif\w*|qualif\w*|"
    r"shortlist\w*|verdict|we will move (?:you )?forward|not moving forward|"
    # performance / competence commentary
    r"competen\w*|incompeten\w*|(?:strong|weak|good|great|excellent|poor|bad)\s+"
    r"(?:candidate|answer|performance|fit)|impressive|unimpressive|promising|"
    r"underqualified|overqualified|unqualified|not\s+a?\s*fit|failing|failed|"
    r"fail(?:ure|s)?\b|pass(?:ed|ing)?\b|score[ds]?|scoring|evaluat\w*|assess\w*|"
    r"rating|ranked|ranking|challenge\w*|"
    # protected / personal-attribute vocabulary
    r"emotion\w*|sentiment|mood|accent\w*|tone of voice|personality|personalit\w*|"
    r"rac(?:e|ial|ism)|ethnic\w*|gender|sexuality|sexual|religio\w*|nationalit\w*|"
    r"orientation|disabilit\w*|disabled|disorder\w*|age\b|aging|appearance|attractiv\w*|"
    r"weight|body language|facial|voice print|biometric\w*"
    r")\b",
    re.IGNORECASE,
)

_FILLER_WORDS = frozenset({"ah", "er", "erm", "hmm", "hm", "uh", "um", "well"})
_INCOMPLETE_ENDINGS = frozenset({
    "and", "because", "but", "if", "or", "so", "when", "where", "which", "while",
})
_INCOMPLETE_PHRASE_ENDINGS = (
    "for example", "i started by", "i think the main", "my role was", "one example is",
    "the main challenge was", "the reason was", "the result was", "we decided to",
    "what happened was", "i was responsible for",
)


def _normalize_utterance(text: str) -> str:
    return " ".join(text.casefold().split()).strip(" .!?,;:")


def _is_filler_only(normalized: str) -> bool:
    if not normalized:
        return True
    words = re.findall(r"[a-z']+", normalized)
    return bool(words) and all(word in _FILLER_WORDS for word in words)


def _is_incomplete_utterance(text: str) -> bool:
    stripped = text.strip()
    normalized = _normalize_utterance(stripped)
    if not normalized:
        return False
    if stripped.endswith(("...", "…", "—", "--")):
        return True
    if normalized.endswith(_INCOMPLETE_PHRASE_ENDINGS):
        return True
    words = normalized.split()
    return len(words) >= 2 and words[-1] in _INCOMPLETE_ENDINGS


def _join_utterances(pending: str | None, current: str) -> str:
    if not pending:
        return current.strip()
    left = pending.rstrip().rstrip(".…—-").rstrip()
    right = current.strip()
    right_words = right.split()
    while right_words and right_words[0].casefold().strip(".,!?;:") in _FILLER_WORDS:
        right_words.pop(0)
    return " ".join(part for part in (left, " ".join(right_words)) if part)


def _shorten_spoken_question(question: str) -> str:
    """Present one clear question while preserving the approved prompt's first intent."""
    spoken = " ".join(question.split()).strip()
    spoken = re.sub(
        r"^Thanks for joining(?:,\s+[^.]+)?\.\s*",
        "",
        spoken,
        flags=re.IGNORECASE,
    )
    spoken = re.sub(r"^(?:could|can) you please\s+", "Could you ", spoken, flags=re.IGNORECASE)

    first_question = spoken.find("?")
    if first_question != -1 and first_question < len(spoken) - 1:
        spoken = spoken[: first_question + 1]

    overload = re.search(
        r"(?:\s+(?:--|—)\s+|,\s*including\b|"
        r",\s*and\s+(?:how|what|why|which|when|where|who)\b|"
        r"\s+and\s+(?:how|what|why|which|when|where|who)\b|;)",
        spoken,
        flags=re.IGNORECASE,
    )
    if overload and overload.start() >= 20:
        spoken = spoken[: overload.start()].rstrip(" ,;:-") + "?"

    if len(spoken) > 180:
        boundary = spoken.find(",", 80)
        if boundary != -1:
            spoken = spoken[:boundary].rstrip(" ,;:-") + "?"
    spoken = spoken.rstrip(" .!?")
    return f"{spoken}?"


def _looks_like_concrete_example(answer: str) -> bool:
    normalized = _normalize_utterance(answer)
    return bool(re.search(r"\b\d+(?:\.\d+)?%?\b", normalized)) or any(
        marker in normalized
        for marker in (
            "for example", "for instance", "i built", "i led", "i implemented",
            "we built", "we implemented", "the result was", "as a result",
        )
    )


def _is_request_to_ask(normalized: str) -> bool:
    return normalized in {
        "can i ask a question", "could i ask a question", "may i ask a question",
        "i have a question", "can i ask you something",
    }


def resolve_voice_persona(
    agent_config_repo: AgentConfigRepository,
    owner_id: int,
    position_id: int,
    company_name: str,
) -> VoiceAgentPersona:
    """Resolve the same active persona for both voice agent and candidate API."""
    configs = [
        config
        for config in agent_config_repo.list(owner_id=owner_id)
        if config.is_active and config.position_id in {None, position_id}
    ]
    ordered = sorted(
        configs,
        key=lambda config: (config.position_id == position_id, config.id or 0),
        reverse=True,
    )
    for config in ordered:
        try:
            return VoiceAgentPersona.model_validate(config.config)
        except ValidationError:
            continue
    return default_persona(company_name=company_name)
