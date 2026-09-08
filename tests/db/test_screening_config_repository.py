"""PositionScreeningConfigRepository: lazy creation, upsert, and revoke_approval.

revoke_approval is the primitive PositionService/PositionQuestionService call
whenever position or question content changes (see
application/position_publishing_service.py's docstring) -- its correctness here
is what keeps a stale approval from ever surviving an edit.
"""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.common import TemplateStatus
from models.platform import HRUser, Position, PositionScreeningConfig
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.screening_configs import SQLAlchemyPositionScreeningConfigRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyPositionScreeningConfigRepository:
    return SQLAlchemyPositionScreeningConfigRepository(db_session_factory)


@pytest.fixture()
def owner(db_session_factory: sessionmaker[Session]) -> HRUser:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email="owner-screening-config@acme.example", password_hash="hashed", full_name="Owner",
    ))


@pytest.fixture()
def position(db_session_factory: sessionmaker[Session], owner: HRUser) -> Position:
    return SQLAlchemyPositionRepository(db_session_factory).create(Position(
        owner_id=owner.id, company_name="Acme", title="Junior AI Engineer",
    ))


def test_get_for_position_returns_none_when_no_row_exists(
    repo: SQLAlchemyPositionScreeningConfigRepository, position: Position,
) -> None:
    assert repo.get_for_position(position.id) is None


def test_upsert_creates_a_row_with_the_requested_fields(
    repo: SQLAlchemyPositionScreeningConfigRepository, position: Position,
) -> None:
    created = repo.upsert(PositionScreeningConfig(position_id=position.id, require_phone=True))
    assert created.id is not None
    assert created.position_id == position.id
    assert created.template_status is TemplateStatus.DRAFT
    assert created.require_phone is True
    # Every automation default matches today's (pre-feature) behavior.
    assert created.accept_public_applications is False
    assert created.auto_email_invitation is False


def test_upsert_updates_the_existing_row_rather_than_creating_a_second_one(
    repo: SQLAlchemyPositionScreeningConfigRepository, position: Position,
) -> None:
    first = repo.upsert(PositionScreeningConfig(position_id=position.id))
    second = repo.upsert(PositionScreeningConfig(position_id=position.id, require_phone=True))
    assert second.id == first.id
    assert repo.get_for_position(position.id).require_phone is True


def test_reminder_offsets_hours_round_trip_through_jsonb(
    repo: SQLAlchemyPositionScreeningConfigRepository, position: Position,
) -> None:
    repo.upsert(PositionScreeningConfig(position_id=position.id, reminder_offsets_hours=[24, 72]))
    assert repo.get_for_position(position.id).reminder_offsets_hours == [24, 72]


def test_approval_fields_and_auto_queue_id_round_trip(
    repo: SQLAlchemyPositionScreeningConfigRepository, position: Position, owner: HRUser,
) -> None:
    from datetime import datetime, timezone

    approved_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    repo.upsert(PositionScreeningConfig(
        position_id=position.id,
        template_status=TemplateStatus.APPROVED,
        template_approved_at=approved_at,
        template_approved_by=owner.id,
        auto_queue_id=None,
    ))
    fetched = repo.get_for_position(position.id)
    assert fetched.template_status is TemplateStatus.APPROVED
    assert fetched.template_approved_at == approved_at
    assert fetched.template_approved_by == owner.id


def test_revoke_approval_on_a_missing_row_is_a_no_op(
    repo: SQLAlchemyPositionScreeningConfigRepository, position: Position,
) -> None:
    repo.revoke_approval(position.id)
    assert repo.get_for_position(position.id) is None


def test_revoke_approval_returns_an_approved_template_to_draft_and_stops_accepting_applications(
    repo: SQLAlchemyPositionScreeningConfigRepository, position: Position, owner: HRUser,
) -> None:
    from datetime import datetime, timezone

    repo.upsert(PositionScreeningConfig(
        position_id=position.id,
        template_status=TemplateStatus.APPROVED,
        template_approved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        template_approved_by=owner.id,
        accept_public_applications=True,
    ))

    repo.revoke_approval(position.id)

    revoked = repo.get_for_position(position.id)
    assert revoked.template_status is TemplateStatus.DRAFT
    assert revoked.template_approved_at is None
    assert revoked.template_approved_by is None
    assert revoked.accept_public_applications is False


def test_revoke_approval_on_an_already_draft_row_leaves_other_settings_untouched(
    repo: SQLAlchemyPositionScreeningConfigRepository, position: Position,
) -> None:
    repo.upsert(PositionScreeningConfig(position_id=position.id, require_phone=True))
    repo.revoke_approval(position.id)
    assert repo.get_for_position(position.id).require_phone is True
