"""Position question use-case logic: ownership-enforced CRUD and reordering.

Ownership is never stored on a question directly -- it's resolved by walking to
the owning position (question -> position -> owner), the same way PATCH/DELETE
/api/v1/questions/{id} has no position id in the URL to check against.
"""
from application.position_question_policy import validate_position_question_content
from application.position_service import PositionService
from models.common import QuestionCategory
from models.platform import HRUser, PositionQuestionRecord
from services.db.questions import PositionQuestionRepository
from services.db.screening_configs import PositionScreeningConfigRepository


class PositionQuestionService:
    def __init__(
        self,
        question_repo: PositionQuestionRepository,
        position_service: PositionService,
        screening_config_repo: PositionScreeningConfigRepository | None = None,
    ) -> None:
        self._question_repo = question_repo
        self._position_service = position_service
        # Optional for the same reason as PositionService's -- see there. A bank
        # question is part of the screening template (SPEC 7), so any add, edit,
        # delete, or reorder must revoke a prior approval exactly like editing
        # the position's own JD/rubric fields does.
        self._screening_config_repo = screening_config_repo

    def list_for_position(self, owner: HRUser, position_id: int) -> list[PositionQuestionRecord]:
        self._position_service.get(owner, position_id)
        return self._question_repo.list_for_position(position_id)

    def add(
        self,
        owner: HRUser,
        position_id: int,
        *,
        category: QuestionCategory,
        question: str,
        order: int,
        purpose: str | None = None,
        expected_topics: list[str] | None = None,
        difficulty: str = "medium",
        follow_up_allowed: bool = True,
    ) -> PositionQuestionRecord:
        self._position_service.get(owner, position_id)
        validate_position_question_content(
            category=category,
            question=question,
            purpose=purpose,
            expected_topics=expected_topics or [],
        )
        record = PositionQuestionRecord(
            position_id=position_id,
            category=category,
            question=question,
            order=order,
            purpose=purpose,
            expected_topics=expected_topics or [],
            difficulty=difficulty,
            follow_up_allowed=follow_up_allowed,
        )
        saved = self._question_repo.add(record)
        self._revoke_approval(position_id)
        return saved

    def update(self, owner: HRUser, question_id: int, updates: dict) -> PositionQuestionRecord:
        existing = self._get_owned(owner, question_id)
        updated = existing.model_copy(update=updates)
        validate_position_question_content(
            category=updated.category,
            question=updated.question,
            purpose=updated.purpose,
            expected_topics=updated.expected_topics,
        )
        saved = self._question_repo.update(updated)
        self._revoke_approval(existing.position_id)
        return saved

    def delete(self, owner: HRUser, question_id: int) -> None:
        existing = self._get_owned(owner, question_id)
        self._question_repo.delete(question_id)
        self._revoke_approval(existing.position_id)

    def reorder(
        self, owner: HRUser, position_id: int, ordered_question_ids: list[int],
    ) -> list[PositionQuestionRecord]:
        self._position_service.get(owner, position_id)
        reordered = self._question_repo.reorder_for_position(position_id, ordered_question_ids)
        self._revoke_approval(position_id)
        return reordered

    def _revoke_approval(self, position_id: int) -> None:
        if self._screening_config_repo is not None:
            self._screening_config_repo.revoke_approval(position_id)

    def _get_owned(self, owner: HRUser, question_id: int) -> PositionQuestionRecord:
        question = self._question_repo.get(question_id)
        self._position_service.get(owner, question.position_id)  # raises ForbiddenError/NotFoundError
        return question
