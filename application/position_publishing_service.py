"""Position screening template approval, publish/unpublish, and slug minting.

This is the ONLY place a position's public_slug is minted or its screening
template is approved/published -- everything a recruiter can do on the
Screening setup tab lives here, mirroring how PositionQuestionService is the
only place a bank question changes.

Template approval and publish are deliberately separate actions (not one
generic settings PUT): approving means "a human has reviewed this exact
question set", and publishing means "start accepting applications against it".
Collapsing them would make it possible to go live with a template nobody
reviewed, which is exactly the human-in-the-loop gate SPEC 7 exists to protect
(see docs/ARCHITECTURE.md: auto-pipeline plan approval happens at the position level).

Nothing here creates a candidate, parses a CV, or touches an interview -- that
is the application-intake pipeline, a later phase. This module only ever
reaches PositionRepository, PositionScreeningConfigRepository,
PositionQuestionRepository (read-only, to check the template is non-empty),
and QueueRepository (to create the one AUTO queue a published position uses).
"""
import re
import secrets
from datetime import datetime, timezone

from pydantic import BaseModel

from application.position_service import PositionService
from models.common import QueueKind, QueueStatus, TemplateStatus
from models.platform import CallQueueRecord, HRUser, Position, PositionScreeningConfig
from services.db.positions import PositionRepository, PositionSlugConflictError
from services.db.questions import PositionQuestionRepository
from services.db.queues import QueueRepository
from services.db.screening_configs import PositionScreeningConfigRepository

#: Settings a recruiter edits directly. accept_public_applications is
#: deliberately excluded -- it is controlled only by publish()/unpublish(), so
#: it can never be true while public_slug is still null.
EDITABLE_SETTINGS = frozenset({
    "auto_parse_cv", "auto_create_plan", "auto_create_invitation", "allow_immediate_start",
    "auto_email_invitation", "allow_cv_personalization", "invitation_ttl_hours",
    "reminder_offsets_hours", "max_applications_per_day", "require_phone", "application_notice",
})

_SLUG_SUFFIX_ATTEMPTS = 5
_MAX_SLUG_BASE_LENGTH = 60


class PositionPublishingError(Exception):
    """Base class for screening-template/publishing use-case failures."""


class TemplateNotReadyError(PositionPublishingError):
    """Raised when approving an empty template, or publishing an unapproved one."""


class ScreeningSetup(BaseModel):
    """Position + its screening config together -- what the Screening setup tab
    (and the public job page) each need in one read, so neither has to make two
    round trips or reconstruct one from the other."""

    position: Position
    config: PositionScreeningConfig


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PositionPublishingService:
    def __init__(
        self,
        position_service: PositionService,
        position_repo: PositionRepository,
        config_repo: PositionScreeningConfigRepository,
        question_repo: PositionQuestionRepository,
        queue_repo: QueueRepository,
        clock=utc_now,
    ) -> None:
        self._position_service = position_service
        self._position_repo = position_repo
        self._config_repo = config_repo
        self._question_repo = question_repo
        self._queue_repo = queue_repo
        self._clock = clock

    # -- reads ---------------------------------------------------------------

    def get_screening_setup(self, owner: HRUser, position_id: int) -> ScreeningSetup:
        position = self._position_service.get(owner, position_id)
        return ScreeningSetup(position=position, config=self._get_or_create_config(position_id))

    # -- settings --------------------------------------------------------------

    def update_settings(self, owner: HRUser, position_id: int, updates: dict) -> ScreeningSetup:
        position = self._position_service.get(owner, position_id)
        config = self._get_or_create_config(position_id)
        unknown = updates.keys() - EDITABLE_SETTINGS
        if unknown:
            raise ValueError(f"Not an editable screening setting: {', '.join(sorted(unknown))}")
        updated = config.model_copy(update=updates)
        saved = self._config_repo.upsert(updated)
        return ScreeningSetup(position=position, config=saved)

    # -- template lifecycle ------------------------------------------------

    def approve_template(self, owner: HRUser, position_id: int) -> ScreeningSetup:
        position = self._position_service.get(owner, position_id)
        config = self._get_or_create_config(position_id)
        bank = self._question_repo.list_for_position(position_id)
        if not bank:
            raise TemplateNotReadyError(
                "Add at least one interview question before approving this position's "
                "screening template."
            )
        approved = config.model_copy(update={
            "template_status": TemplateStatus.APPROVED,
            "template_approved_at": self._clock(),
            "template_approved_by": owner.id,
        })
        saved = self._config_repo.upsert(approved)
        return ScreeningSetup(position=position, config=saved)

    def publish(self, owner: HRUser, position_id: int) -> ScreeningSetup:
        """Start accepting public applications: mint the slug and the auto queue
        the first time, then flip accept_public_applications on.

        Idempotent -- calling this on an already-published position mints
        nothing new and simply confirms the flag is set, so a recruiter re-
        clicking Publish (or a retried request) never creates a second slug or
        a second queue.
        """
        position = self._position_service.get(owner, position_id)
        config = self._get_or_create_config(position_id)
        if not config.is_template_approved:
            raise TemplateNotReadyError(
                "Approve this position's screening template before publishing it."
            )

        if position.public_slug is None:
            position = self._assign_slug(position)

        if config.auto_queue_id is None:
            queue = self._queue_repo.create_queue(CallQueueRecord(
                position_id=position_id,
                name=f"Auto queue — {position.title}",
                status=QueueStatus.RUNNING,
                kind=QueueKind.AUTO,
            ))
            config = config.model_copy(update={"auto_queue_id": queue.id})

        config = config.model_copy(update={"accept_public_applications": True})
        saved = self._config_repo.upsert(config)
        return ScreeningSetup(position=position, config=saved)

    def unpublish(self, owner: HRUser, position_id: int) -> ScreeningSetup:
        """Stop accepting new applications. The slug, the auto queue, and the
        template approval are all left intact so re-publishing is instant and
        any invitation already issued (a later phase) keeps working."""
        position = self._position_service.get(owner, position_id)
        config = self._get_or_create_config(position_id)
        updated = config.model_copy(update={"accept_public_applications": False})
        saved = self._config_repo.upsert(updated)
        return ScreeningSetup(position=position, config=saved)

    # -- internals -----------------------------------------------------------

    def _get_or_create_config(self, position_id: int) -> PositionScreeningConfig:
        existing = self._config_repo.get_for_position(position_id)
        if existing is not None:
            return existing
        return self._config_repo.upsert(PositionScreeningConfig(position_id=position_id))

    def _assign_slug(self, position: Position) -> Position:
        base = _slugify(position.title)
        last_error: PositionSlugConflictError | None = None
        for _ in range(_SLUG_SUFFIX_ATTEMPTS):
            candidate = f"{base}-{secrets.token_hex(4)}"
            try:
                return self._position_repo.update(
                    position.model_copy(update={"public_slug": candidate})
                )
            except PositionSlugConflictError as exc:
                last_error = exc
        raise PositionPublishingError(
            "Could not generate a unique application link; please try again."
        ) from last_error


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return slug[:_MAX_SLUG_BASE_LENGTH] or "position"
