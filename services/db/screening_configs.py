"""Position screening config repository: interface + PostgreSQL-backed implementation.

One row per position (see models.platform.PositionScreeningConfig), created
lazily by PositionPublishingService the first time a recruiter reads or edits it
-- there is no row for a position that has never opened its Screening setup tab.
"""
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models.common import TemplateStatus
from models.platform import PositionScreeningConfig
from services.db.orm_models import PositionScreeningConfigRow


class PositionScreeningConfigRepository(ABC):
    @abstractmethod
    def get_for_position(self, position_id: int) -> PositionScreeningConfig | None: ...

    @abstractmethod
    def upsert(self, config: PositionScreeningConfig) -> PositionScreeningConfig: ...

    @abstractmethod
    def revoke_approval(self, position_id: int) -> None: ...


class SQLAlchemyPositionScreeningConfigRepository(PositionScreeningConfigRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_for_position(self, position_id: int) -> PositionScreeningConfig | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(PositionScreeningConfigRow).where(
                    PositionScreeningConfigRow.position_id == position_id
                )
            )
            return _to_model(row) if row is not None else None

    def upsert(self, config: PositionScreeningConfig) -> PositionScreeningConfig:
        with self._session_factory() as session:
            row = session.scalar(
                select(PositionScreeningConfigRow).where(
                    PositionScreeningConfigRow.position_id == config.position_id
                )
            )
            if row is None:
                row = PositionScreeningConfigRow(position_id=config.position_id)
                session.add(row)
            row.template_status = config.template_status.value
            row.template_approved_at = config.template_approved_at
            row.template_approved_by = config.template_approved_by
            row.auto_queue_id = config.auto_queue_id
            row.accept_public_applications = config.accept_public_applications
            row.auto_parse_cv = config.auto_parse_cv
            row.auto_create_plan = config.auto_create_plan
            row.auto_create_invitation = config.auto_create_invitation
            row.allow_immediate_start = config.allow_immediate_start
            row.auto_email_invitation = config.auto_email_invitation
            row.allow_cv_personalization = config.allow_cv_personalization
            row.invitation_ttl_hours = config.invitation_ttl_hours
            row.reminder_offsets_hours = list(config.reminder_offsets_hours)
            row.max_applications_per_day = config.max_applications_per_day
            row.require_phone = config.require_phone
            row.application_notice = config.application_notice
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def revoke_approval(self, position_id: int) -> None:
        """Return an approved template to draft and stop accepting applications.

        Called by PositionService/PositionQuestionService whenever position or
        question content changes, so an approval can never silently go stale. A
        no-op when no config row exists yet -- nothing was approved to revoke.
        """
        with self._session_factory() as session:
            row = session.scalar(
                select(PositionScreeningConfigRow).where(
                    PositionScreeningConfigRow.position_id == position_id
                )
            )
            if row is None or row.template_status == TemplateStatus.DRAFT.value:
                return
            row.template_status = TemplateStatus.DRAFT.value
            row.template_approved_at = None
            row.template_approved_by = None
            row.accept_public_applications = False
            session.commit()


def _to_model(row: PositionScreeningConfigRow) -> PositionScreeningConfig:
    return PositionScreeningConfig(
        id=row.id,
        position_id=row.position_id,
        template_status=TemplateStatus(row.template_status),
        template_approved_at=row.template_approved_at,
        template_approved_by=row.template_approved_by,
        auto_queue_id=row.auto_queue_id,
        accept_public_applications=row.accept_public_applications,
        auto_parse_cv=row.auto_parse_cv,
        auto_create_plan=row.auto_create_plan,
        auto_create_invitation=row.auto_create_invitation,
        allow_immediate_start=row.allow_immediate_start,
        auto_email_invitation=row.auto_email_invitation,
        allow_cv_personalization=row.allow_cv_personalization,
        invitation_ttl_hours=row.invitation_ttl_hours,
        reminder_offsets_hours=list(row.reminder_offsets_hours),
        max_applications_per_day=row.max_applications_per_day,
        require_phone=row.require_phone,
        application_notice=row.application_notice,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
