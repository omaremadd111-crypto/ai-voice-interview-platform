"""Phase 2: no-response resolution through the conversation layer.

Contract under test (plan sections 10/22): when the silence ladder exhausts,
the interview advances WITHOUT fabricating candidate speech -- an empty string
goes through the normal engine path, or (race R5) the buffered fragment of
real speech is submitted instead of being discarded. Synthetic placeholders
are forbidden.
"""
from types import SimpleNamespace

from models.common import InterviewState
from models.interview import NextPrompt
from models.platform import CandidateRecord, Position

from application.voice_conversation_service import VoiceConversationService


class FakeAgentService:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.ended = False
        self.next_prompt = NextPrompt(
            state=InterviewState.IN_PROGRESS,
            question_id="q1",
            question_text="Tell me about a project you led?",
            total_questions=3,
        )
        self.queue: list[NextPrompt] = []

    def start_interview(self, session_id: str) -> NextPrompt:
        return self.next_prompt

    def submit_answer(self, session_id: str, answer: str, *, intent_hint=None) -> NextPrompt:
        self.submitted.append(answer)
        if self.queue:
            self.next_prompt = self.queue.pop(0)
        return self.next_prompt

    def get_current_prompt(self, session_id: str) -> NextPrompt:
        return self.next_prompt

    def end_interview(self, session_id: str) -> object:
        self.ended = True
        return object()


def build_service(agent_cls=FakeAgentService, session_id: str = "s1"):
    agent = agent_cls()
    candidate = CandidateRecord(id=4, position_id=3, full_name="Sam Taylor")
    position = Position(id=3, owner_id=2, company_name="Acme", title="Engineer")
    service = VoiceConversationService(
        agent,  # type: ignore[arg-type]
        SimpleNamespace(get=lambda _: candidate),
        SimpleNamespace(get=lambda _: position),
        SimpleNamespace(list=lambda *, owner_id, position_id=None: []),  # type: ignore[arg-type]
        None,
    )
    context = service.load_context(session_id=session_id, candidate_id=4, position_id=3)
    return service, agent, context


def test_no_response_submits_empty_answer_and_speaks_next_question() -> None:
    service, agent, context = build_service()
    second_question = NextPrompt(
        state=InterviewState.IN_PROGRESS,
        question_id="q2",
        question_text="Describe a technical challenge you solved.",
        question_index=1,
        total_questions=3,
    )
    agent.queue.append(second_question)

    reply = service.resolve_no_response(context, nudge_count=2, elapsed_seconds=45.0)

    # Transcript fidelity: an EMPTY answer is submitted -- never "[no response]"
    # or any other synthetic text standing in for words nobody said.
    assert agent.submitted == [""]
    assert reply.segments[-1] == "Describe a technical challenge you solved?"
    assert all("no response" not in segment.lower() for segment in reply.segments)


def test_r5_pending_buffer_is_submitted_instead_of_being_discarded() -> None:
    service, agent, context = build_service()
    # The candidate started an answer that endpointing split mid-thought...
    service.respond(context, "I was responsible for")
    # ...then went silent with the buffer still open.
    second_question = NextPrompt(
        state=InterviewState.IN_PROGRESS,
        question_id="q2",
        question_text="Next question?",
        question_index=1,
        total_questions=3,
    )
    agent.queue.append(second_question)

    reply = service.resolve_no_response(context, nudge_count=1, elapsed_seconds=47.0)

    # Their real words are NOT silently dropped: the buffered fragment is what
    # gets recorded, verbatim.
    assert agent.submitted == ["I was responsible for"]
    assert reply.segments[-1] == "Next question?"


def test_no_response_on_final_question_delivers_closing_without_new_question() -> None:
    service, agent, context = build_service()
    completion = NextPrompt(
        state=InterviewState.COMPLETED,
        question_id="q3",
        question_text="Any questions?",
        finished=True,
        total_questions=3,
    )
    agent.queue.append(completion)

    reply = service.resolve_no_response(context, nudge_count=2, elapsed_seconds=50.0)

    assert agent.submitted == [""]
    assert reply.finished is True
    assert "Sam" in reply.segments[0]


def test_no_response_after_interview_already_ended_submits_nothing() -> None:
    from services.interview_engine import InvalidInterviewStateError

    class EndedAgentService(FakeAgentService):
        def get_current_prompt(self, session_id: str) -> NextPrompt:
            raise InvalidInterviewStateError("Cannot read a current prompt from COMPLETED.")

    service, agent, context = build_service(agent_cls=EndedAgentService)

    reply = service.resolve_no_response(context, nudge_count=1, elapsed_seconds=46.0)

    assert reply.segments == ()
    assert agent.submitted == []
    assert agent.ended is False


def test_restate_question_repeats_approved_prompt_verbatim_without_submitting() -> None:
    service, agent, context = build_service()

    reply = service.restate_question(context)

    assert reply.segments == ("Tell me about a project you led?",)
    assert reply.question_id == "q1"
    assert agent.submitted == []
