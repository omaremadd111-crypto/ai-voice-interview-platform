"""Ownership-safe read access to persisted post-interview results.

This service contains no evaluation, scoring, or report-building logic. The queue
worker produces those artifacts through InterviewAgentService; this read side
only locates the resulting PostgreSQL snapshot for recruiter-facing transports.
"""

from application.candidate_service import CandidateService
from application.queue_service import QueueService
from models.evaluation import HRReport
from models.platform import HRUser
from services.db.evaluations import EvaluationRepository


class ScreeningResultService:
    def __init__(
        self,
        candidate_service: CandidateService,
        queue_service: QueueService,
        evaluation_repo: EvaluationRepository,
    ) -> None:
        self._candidate_service = candidate_service
        self._queue_service = queue_service
        self._evaluation_repo = evaluation_repo

    def latest_for_candidate(self, owner: HRUser, candidate_id: int) -> HRReport | None:
        # CandidateService proves the candidate belongs to one of this recruiter's
        # positions before any assessment data is queried.
        self._candidate_service.get(owner, candidate_id)
        evaluation = self._evaluation_repo.get_latest_for_candidate(candidate_id)
        return _copy_report(evaluation.report if evaluation is not None else None)

    def for_queue_item(
        self, owner: HRUser, queue_id: int, item_id: int,
    ) -> HRReport | None:
        item = self._queue_service.get_item(owner, queue_id, item_id)
        if item.interview_session_id is None:
            return None
        evaluation = self._evaluation_repo.get_for_session(item.interview_session_id)
        return _copy_report(evaluation.report if evaluation is not None else None)


def _copy_report(report: HRReport | None) -> HRReport | None:
    return report.model_copy(deep=True) if report is not None else None
