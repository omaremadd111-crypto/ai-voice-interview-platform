"""The public, unauthenticated read path onto a published position.

This is the only application service with no `owner: HRUser` parameter, and
that absence is the point: it is reached from api/routers/public.py, which
carries no session at all. Every other application service proves ownership
before returning anything; this one instead proves the position is meant to be
public -- template approved AND accept_public_applications -- before returning
anything, and returns the identical "not available" outcome whether the slug
does not exist, the position was never published, or it was unpublished, so a
public caller can never distinguish "wrong link" from "not open yet".

Returns only fields a job posting is meant to show. It must never expose
rubric_profile, pass_score_threshold, owner_id, questions, or any candidate or
evaluation data -- see tests/api/test_public_jobs.py and
tests/test_governance.py for the checks that keep it that way.
"""
from pydantic import BaseModel

from services.db.positions import PositionRepository
from services.db.screening_configs import PositionScreeningConfigRepository


class PublicJobNotAvailableError(Exception):
    """Raised for a slug that does not exist, or a position not currently
    accepting public applications. Deliberately a single outcome for both."""


class PublicJobListing(BaseModel):
    slug: str
    title: str
    company_name: str
    description: str | None
    experience_level: str | None
    require_phone: bool
    application_notice: str | None


class PublicJobService:
    def __init__(
        self,
        position_repo: PositionRepository,
        config_repo: PositionScreeningConfigRepository,
    ) -> None:
        self._position_repo = position_repo
        self._config_repo = config_repo

    def get_listing(self, slug: str) -> PublicJobListing:
        position = self._position_repo.get_by_slug(slug)
        if position is None:
            raise PublicJobNotAvailableError("This job posting is not available.")
        config = self._config_repo.get_for_position(position.id)
        if config is None or not config.is_published:
            raise PublicJobNotAvailableError("This job posting is not available.")
        return PublicJobListing(
            slug=slug,
            title=position.title,
            company_name=position.company_name,
            description=position.description,
            experience_level=position.experience_level,
            require_phone=config.require_phone,
            application_notice=config.application_notice,
        )
