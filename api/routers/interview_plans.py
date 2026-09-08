"""Candidate interview plan endpoints: thin transport only.

Generation, merging with the position's question bank, deduplication, and the
approval rule all live in application/interview_plan_service.py.
"""
from fastapi import APIRouter, Depends

from api.dependencies import get_current_user, get_interview_plan_service
from api.schemas.interview_plan import (
    GeneratePlanRequest,
    InterviewPlanResponse,
    PlanQuestionResponse,
    SavePlanRequest,
)
from application.interview_plan_service import InterviewPlanService, PlanNotFoundError
from models.platform import CandidateInterviewPlan, CandidatePlanQuestion, HRUser

router = APIRouter(prefix="/api/v1/candidates", tags=["interview-plan"])


@router.get("/{candidate_id}/interview-plan", response_model=InterviewPlanResponse)
def get_plan(
    candidate_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: InterviewPlanService = Depends(get_interview_plan_service),
) -> InterviewPlanResponse:
    plan = service.get(current_user, candidate_id)
    if plan is None:
        raise PlanNotFoundError(
            f"Candidate {candidate_id} has no interview plan yet. Generate one first."
        )
    return _to_response(plan)


@router.post("/{candidate_id}/interview-plan/generate", response_model=InterviewPlanResponse)
def generate_plan(
    candidate_id: int,
    payload: GeneratePlanRequest,
    current_user: HRUser = Depends(get_current_user),
    service: InterviewPlanService = Depends(get_interview_plan_service),
) -> InterviewPlanResponse:
    plan = service.generate(current_user, candidate_id, num_questions=payload.num_questions)
    return _to_response(plan)


@router.put("/{candidate_id}/interview-plan", response_model=InterviewPlanResponse)
def save_plan(
    candidate_id: int,
    payload: SavePlanRequest,
    current_user: HRUser = Depends(get_current_user),
    service: InterviewPlanService = Depends(get_interview_plan_service),
) -> InterviewPlanResponse:
    questions = [
        CandidatePlanQuestion(order=index, **question.model_dump())
        for index, question in enumerate(payload.questions)
    ]
    return _to_response(service.save_questions(current_user, candidate_id, questions))


@router.post(
    "/{candidate_id}/interview-plan/questions/{index}/regenerate",
    response_model=InterviewPlanResponse,
)
def regenerate_question(
    candidate_id: int,
    index: int,
    current_user: HRUser = Depends(get_current_user),
    service: InterviewPlanService = Depends(get_interview_plan_service),
) -> InterviewPlanResponse:
    return _to_response(service.regenerate_question(current_user, candidate_id, index))


@router.post("/{candidate_id}/interview-plan/approve", response_model=InterviewPlanResponse)
def approve_plan(
    candidate_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: InterviewPlanService = Depends(get_interview_plan_service),
) -> InterviewPlanResponse:
    return _to_response(service.approve(current_user, candidate_id))


def _to_response(plan: CandidateInterviewPlan) -> InterviewPlanResponse:
    return InterviewPlanResponse(
        candidate_id=plan.candidate_id,
        status=plan.status,
        questions=[
            PlanQuestionResponse(
                order=question.order,
                category=question.category,
                question=question.question,
                purpose=question.purpose,
                expected_topics=question.expected_topics,
                difficulty=question.difficulty,
                follow_up_allowed=question.follow_up_allowed,
                source=question.source,
            )
            for question in sorted(plan.questions, key=lambda q: q.order)
        ],
        generated_at=plan.generated_at,
        approved_at=plan.approved_at,
    )
