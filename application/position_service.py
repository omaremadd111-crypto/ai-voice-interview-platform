"""Position use-case logic: ownership-enforced CRUD over PositionRepository.

Routers never call PositionRepository directly -- they go through this service,
which is the single place that enforces "a user must never access another
user's position."
"""
from application.auth_service import ForbiddenError
from models.common import PositionStatus
from models.platform import HRUser, Position
from services.db.positions import PositionRepository
from services.db.screening_configs import PositionScreeningConfigRepository

#: Position fields that are part of a screening template (docs/ARCHITECTURE.md
#: "Auto-pipeline plan approval happens at the position level"): changing any of
#: these is a content change to what a recruiter approved, so it must revoke
#: that approval exactly like editing a bank question already does. `status` is
#: deliberately excluded -- it is the recruiter's own draft/active/closed
#: lifecycle field, orthogonal to the screening template's content.
_TEMPLATE_FIELDS = frozenset({
    "company_name", "title", "description", "experience_level",
    "pass_score_threshold", "rubric_profile",
})


class PositionService:
    def __init__(
        self,
        position_repo: PositionRepository,
        screening_config_repo: PositionScreeningConfigRepository | None = None,
    ) -> None:
        self._position_repo = position_repo
        # Optional so every existing construction (and every existing test) keeps
        # working unchanged -- see application/voice_invite_service.py's
        # consent_repo for the same pattern. Only wired in api/dependencies.py.
        self._screening_config_repo = screening_config_repo

    def create(
        self,
        owner: HRUser,
        *,
        company_name: str,
        title: str,
        description: str | None = None,
        experience_level: str | None = None,
        pass_score_threshold: int | None = None,
        rubric_profile: str | None = None,
        status: PositionStatus = PositionStatus.DRAFT,
    ) -> Position:
        position = Position(
            owner_id=owner.id,
            company_name=company_name,
            title=title,
            description=description,
            experience_level=experience_level,
            pass_score_threshold=pass_score_threshold,
            rubric_profile=rubric_profile,
            status=status,
        )
        return self._position_repo.create(position)

    def get(self, owner: HRUser, position_id: int) -> Position:
        position = self._position_repo.get(position_id)
        _require_owner(owner, position)
        return position

    def list(self, owner: HRUser, *, status: PositionStatus | None = None) -> list[Position]:
        return self._position_repo.list(owner_id=owner.id, status=status)

    def update(self, owner: HRUser, position_id: int, updates: dict) -> Position:
        position = self.get(owner, position_id)
        updated = position.model_copy(update=updates)
        saved = self._position_repo.update(updated)
        if self._screening_config_repo is not None and _TEMPLATE_FIELDS & updates.keys():
            self._screening_config_repo.revoke_approval(position_id)
        return saved

    def delete(self, owner: HRUser, position_id: int) -> None:
        self.get(owner, position_id)
        self._position_repo.delete(position_id)


def _require_owner(owner: HRUser, position: Position) -> None:
    if position.owner_id != owner.id:
        raise ForbiddenError("You do not have access to this position")
