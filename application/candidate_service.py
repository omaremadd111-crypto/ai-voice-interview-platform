"""Candidate use-case logic: ownership-enforced CRUD via the owning position.

CV handling has two equivalent entry points -- an uploaded file and pasted text --
and both end up in the same place: CandidateRecord.cv_text. Uploaded files are
extracted by the EXISTING services/document_parser.DocumentParser; this module
never parses a document itself.
"""
from application.position_service import PositionService
from models.platform import CandidateRecord, HRUser
from services.db.candidates import CandidateRepository
from services.document_parser import DocumentParser


class CandidateServiceError(Exception):
    """Base class for candidate use-case failures."""


class EmptyUploadError(CandidateServiceError):
    """Raised when an uploaded CV file contains no bytes."""


class UploadTooLargeError(CandidateServiceError):
    """Raised when an uploaded CV exceeds settings.max_cv_upload_bytes."""


class CandidateService:
    def __init__(
        self,
        candidate_repo: CandidateRepository,
        position_service: PositionService,
        document_parser: DocumentParser,
        max_cv_upload_bytes: int,
    ) -> None:
        self._candidate_repo = candidate_repo
        self._position_service = position_service
        self._document_parser = document_parser
        self._max_cv_upload_bytes = max_cv_upload_bytes

    def create(
        self,
        owner: HRUser,
        position_id: int,
        *,
        full_name: str,
        email: str | None = None,
        phone: str | None = None,
        cv_text: str | None = None,
        cv_filename: str | None = None,
    ) -> CandidateRecord:
        self._position_service.get(owner, position_id)
        record = CandidateRecord(
            position_id=position_id, full_name=full_name, email=email, phone=phone,
            cv_text=cv_text, cv_filename=cv_filename,
        )
        return self._candidate_repo.create(record)

    def get(self, owner: HRUser, candidate_id: int) -> CandidateRecord:
        candidate = self._candidate_repo.get(candidate_id)
        self._position_service.get(owner, candidate.position_id)
        return candidate

    def list_for_position(self, owner: HRUser, position_id: int) -> list[CandidateRecord]:
        self._position_service.get(owner, position_id)
        return self._candidate_repo.list_for_position(position_id)

    def update(self, owner: HRUser, candidate_id: int, updates: dict) -> CandidateRecord:
        existing = self.get(owner, candidate_id)
        updated = existing.model_copy(update=updates)
        return self._candidate_repo.update(updated)

    def upload_cv(
        self, owner: HRUser, candidate_id: int, *, data: bytes, filename: str,
    ) -> CandidateRecord:
        """Extract an uploaded CV's text and store it on the candidate.

        The size check runs before parsing so an oversized upload is rejected
        without handing it to pypdf/python-docx. UnsupportedDocumentError and
        DocumentParseError propagate unchanged -- api/errors.py maps them to 400,
        so the parser stays the single source of truth on what is readable.
        """
        existing = self.get(owner, candidate_id)
        if not data:
            raise EmptyUploadError("The uploaded file is empty.")
        if len(data) > self._max_cv_upload_bytes:
            raise UploadTooLargeError(
                f"The uploaded file is larger than the "
                f"{self._max_cv_upload_bytes // (1024 * 1024)} MB limit."
            )

        parsed = self._document_parser.parse_bytes(data, filename)
        updated = existing.model_copy(
            update={"cv_text": parsed.text, "cv_filename": parsed.filename},
        )
        return self._candidate_repo.update(updated)
