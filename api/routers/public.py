"""Public, unauthenticated candidate-facing endpoints.

The ONLY router in this application with no `get_current_user` dependency
anywhere in it (aside from health/auth, which are also intentionally public)
-- see tests/test_governance.py's routers-layer checks. Every handler here
takes a slug or an invitation token, never a numeric id a recruiter endpoint
would recognize, and every response schema is api/schemas/public.py, never a
recruiter one.

apply_to_job takes the raw Request instead of FastAPI's usual
UploadFile = File(...) parameter deliberately: that parameter form makes
FastAPI parse the entire multipart body as part of resolving the function's
arguments, before a single line of the handler runs -- too late to reject an
oversized submission by its Content-Length header. Reading the header first
and only then calling await request.form() is what makes that check real.

Only mounted at all when APISettings.public_applications_enabled is true --
see api/app.py.
"""
from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from api.dependencies import (
    get_application_pipeline_service,
    get_interview_landing_service,
    get_public_job_service,
    get_settings,
)
from api.schemas.public import (
    ApplyFields,
    ApplyResponse,
    InterviewLandingResponse,
    PublicJobResponse,
)
from application.application_pipeline_service import ApplicationPipelineService
from application.candidate_service import UploadTooLargeError
from application.interview_landing_service import InterviewLandingService
from application.public_job_service import PublicJobService
from config.settings import Settings

router = APIRouter(prefix="/api/v1/public", tags=["public"])

#: Multipart request-size ceiling: the CV cap plus slack for the other form
#: fields and multipart boundary overhead. Enforced by Content-Length before
#: the body is ever read -- an oversized upload never reaches memory.
_FORM_OVERHEAD_BYTES = 64 * 1024


@router.get("/jobs/{slug}", response_model=PublicJobResponse)
def get_public_job(
    slug: str,
    service: PublicJobService = Depends(get_public_job_service),
) -> PublicJobResponse:
    listing = service.get_listing(slug)
    return PublicJobResponse(**listing.model_dump())


@router.post("/jobs/{slug}/apply", response_model=ApplyResponse, status_code=201)
async def apply_to_job(
    slug: str,
    request: Request,
    service: ApplicationPipelineService = Depends(get_application_pipeline_service),
    settings: Settings = Depends(get_settings),
) -> ApplyResponse:
    max_bytes = settings.max_cv_upload_bytes + _FORM_OVERHEAD_BYTES
    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit() and int(content_length) > max_bytes:
        raise UploadTooLargeError(
            f"The submission is larger than the {max_bytes // (1024 * 1024)} MB limit."
        )

    form = await request.form()
    try:
        fields = ApplyFields(
            full_name=str(form.get("full_name", "")),
            email=str(form.get("email", "")),
            phone=(str(v) if (v := form.get("phone")) else None),
            consent=str(form.get("consent", "")).strip().lower() in {"true", "1", "on", "yes"},
        )
    except ValidationError as exc:
        raise ValueError(_first_error_message(exc)) from exc
    if not fields.consent:
        raise ValueError("You must accept the notice to apply.")

    cv_data: bytes | None = None
    cv_filename: str | None = None
    upload = form.get("cv")
    if upload is not None and hasattr(upload, "read"):
        cv_data = await upload.read()
        cv_filename = upload.filename
        if cv_data and len(cv_data) > settings.max_cv_upload_bytes:
            raise UploadTooLargeError(
                f"The uploaded file is larger than the "
                f"{settings.max_cv_upload_bytes // (1024 * 1024)} MB limit."
            )

    outcome = service.apply(
        slug,
        full_name=fields.full_name,
        email=fields.email,
        phone=fields.phone,
        cv_data=cv_data or None,
        cv_filename=cv_filename,
        submitter_ip=request.client.host if request.client else None,
    )
    return ApplyResponse(application_id=outcome.application_id, interview_token=outcome.interview_token)


@router.get("/interviews/{token}", response_model=InterviewLandingResponse)
def get_interview_landing(
    token: str,
    service: InterviewLandingService = Depends(get_interview_landing_service),
) -> InterviewLandingResponse:
    return InterviewLandingResponse(**service.get_landing(token).model_dump())


@router.post("/interviews/{token}/start", response_model=InterviewLandingResponse)
def start_interview(
    token: str,
    service: InterviewLandingService = Depends(get_interview_landing_service),
) -> InterviewLandingResponse:
    return InterviewLandingResponse(**service.start(token).model_dump())


@router.get("/interviews/{token}/status", response_model=InterviewLandingResponse)
def get_interview_status(
    token: str,
    service: InterviewLandingService = Depends(get_interview_landing_service),
) -> InterviewLandingResponse:
    return InterviewLandingResponse(**service.get_status(token).model_dump())


def _first_error_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid submission."
    first = errors[0]
    field = ".".join(str(part) for part in first["loc"])
    return f"{field}: {first['msg']}" if field else first["msg"]
