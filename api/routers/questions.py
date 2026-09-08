"""Position question endpoints: thin transport only. Ownership enforcement and
persistence orchestration live in application/position_question_service.py.

Two routers because the endpoint set spans two URL families:
/positions/{id}/questions... (position-scoped) and /questions/{id} (by question
id alone, per the requested endpoint list).
"""
from fastapi import APIRouter, Depends

from api.dependencies import (
    get_current_user,
    get_position_question_service,
    get_question_suggestion_service,
)
from api.schemas.question import (
    QuestionCreateRequest,
    QuestionResponse,
    QuestionUpdateRequest,
    ReorderQuestionsRequest,
    SuggestedQuestionResponse,
    SuggestQuestionsRequest,
)
from application.position_question_service import PositionQuestionService
from application.question_suggestion_service import QuestionSuggestionService
from models.platform import HRUser, PositionQuestionRecord

position_questions_router = APIRouter(prefix="/api/v1/positions", tags=["questions"])
questions_router = APIRouter(prefix="/api/v1/questions", tags=["questions"])


@position_questions_router.get("/{position_id}/questions", response_model=list[QuestionResponse])
def list_questions(
    position_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: PositionQuestionService = Depends(get_position_question_service),
) -> list[QuestionResponse]:
    return [_to_response(q) for q in service.list_for_position(current_user, position_id)]


@position_questions_router.post("/{position_id}/questions", response_model=QuestionResponse, status_code=201)
def add_question(
    position_id: int,
    payload: QuestionCreateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: PositionQuestionService = Depends(get_position_question_service),
) -> QuestionResponse:
    question = service.add(current_user, position_id, **payload.model_dump())
    return _to_response(question)


@position_questions_router.put("/{position_id}/questions/reorder", response_model=list[QuestionResponse])
def reorder_questions(
    position_id: int,
    payload: ReorderQuestionsRequest,
    current_user: HRUser = Depends(get_current_user),
    service: PositionQuestionService = Depends(get_position_question_service),
) -> list[QuestionResponse]:
    reordered = service.reorder(current_user, position_id, payload.question_ids)
    return [_to_response(q) for q in reordered]


@position_questions_router.post(
    "/{position_id}/questions/suggest", response_model=list[SuggestedQuestionResponse],
)
def suggest_questions(
    position_id: int,
    payload: SuggestQuestionsRequest,
    current_user: HRUser = Depends(get_current_user),
    service: QuestionSuggestionService = Depends(get_question_suggestion_service),
) -> list[SuggestedQuestionResponse]:
    """Propose questions for HR review. Nothing is saved -- the client persists
    whichever suggestions HR keeps via the normal add-question endpoint."""
    suggestions = service.suggest(
        current_user,
        position_id,
        num_questions=payload.num_questions,
    )
    return [
        SuggestedQuestionResponse(
            suggestion_id=question.id,
            category=question.category,
            question=question.question,
            purpose=question.purpose,
            expected_topics=question.expected_topics,
            difficulty=question.difficulty,
            follow_up_allowed=question.follow_up_allowed,
        )
        for question in suggestions
    ]


@questions_router.patch("/{question_id}", response_model=QuestionResponse)
def update_question(
    question_id: int,
    payload: QuestionUpdateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: PositionQuestionService = Depends(get_position_question_service),
) -> QuestionResponse:
    updates = payload.model_dump(exclude_unset=True)
    return _to_response(service.update(current_user, question_id, updates))


@questions_router.delete("/{question_id}", status_code=204)
def delete_question(
    question_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: PositionQuestionService = Depends(get_position_question_service),
) -> None:
    service.delete(current_user, question_id)


def _to_response(question: PositionQuestionRecord) -> QuestionResponse:
    return QuestionResponse(
        id=question.id,
        position_id=question.position_id,
        category=question.category,
        question=question.question,
        order=question.order,
        purpose=question.purpose,
        expected_topics=question.expected_topics,
        difficulty=question.difficulty,
        follow_up_allowed=question.follow_up_allowed,
    )
