"""P7 read-side orchestration never re-evaluates or bypasses ownership."""

from unittest.mock import Mock

import pytest

from application.auth_service import ForbiddenError
from application.candidate_service import CandidateService
from application.queue_service import QueueService
from application.screening_result_service import ScreeningResultService
from models.common import (
    EvaluationCategory,
    QuestionCategory,
    RecommendationLevel,
    ScreeningOutcome,
)
from models.evaluation import CategoryEvaluation, HRReport
from models.interview import InterviewTurn
from models.platform import EvaluationRecord, HRUser, QueueItemRecord
from services.db.evaluations import EvaluationRepository


def _report() -> HRReport:
    category = CategoryEvaluation(
        category=EvaluationCategory.TECHNICAL_KNOWLEDGE,
        score=None,
        sufficient_evidence=False,
        reasoning="Insufficient evidence",
        areas_to_validate=["Technical depth"],
    )
    return HRReport(
        session_id="session-1",
        company="Acme",
        role="Engineer",
        candidate_name="Demo Candidate",
        candidate_overview="Fictional candidate profile.",
        interview_summary="One evidence area was discussed.",
        overall_score=None,
        evidence_coverage=0.2,
        category_scores=[category],
        strengths=[],
        areas_requiring_validation=["Technical depth"],
        human_follow_up_questions=["Could you provide a concrete technical example?"],
        full_transcript=[InterviewTurn(
            question_id="q1",
            question="Describe a technical project.",
            category=QuestionCategory.TECHNICAL,
            answer="I built a fictional Python service.",
            is_follow_up=False,
            timestamp="2026-08-23T10:00:00+00:00",
        )],
        recommendation=RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW,
        screening_outcome=ScreeningOutcome.NEEDS_REVIEW,
        llm_provider="mock",
        is_mock=True,
        generated_at="2026-08-23T10:01:00+00:00",
    )


def _evaluation(report: HRReport) -> EvaluationRecord:
    return EvaluationRecord(
        interview_session_id=report.session_id,
        overall_score=None,
        evidence_coverage=report.evidence_coverage,
        recommendation=report.recommendation,
        screening_outcome=report.screening_outcome,
        rubric_profile="technical",
        category_results=report.category_scores,
        report=report,
    )


def _service() -> tuple[ScreeningResultService, Mock, Mock, Mock]:
    candidates = Mock(spec=CandidateService)
    queues = Mock(spec=QueueService)
    evaluations = Mock(spec=EvaluationRepository)
    return ScreeningResultService(candidates, queues, evaluations), candidates, queues, evaluations


def test_candidate_result_is_read_from_the_persisted_evaluation_snapshot() -> None:
    service, candidates, _, evaluations = _service()
    owner = HRUser(id=7, email="recruiter@example.test", password_hash="x", full_name="Recruiter")
    report = _report()
    evaluations.get_latest_for_candidate.return_value = _evaluation(report)

    result = service.latest_for_candidate(owner, 42)

    candidates.get.assert_called_once_with(owner, 42)
    evaluations.get_latest_for_candidate.assert_called_once_with(42)
    assert result == report
    assert result is not report


def test_candidate_ownership_is_checked_before_evaluation_data_is_read() -> None:
    service, candidates, _, evaluations = _service()
    owner = HRUser(id=7, email="recruiter@example.test", password_hash="x", full_name="Recruiter")
    candidates.get.side_effect = ForbiddenError("not owned")

    with pytest.raises(ForbiddenError):
        service.latest_for_candidate(owner, 42)

    evaluations.get_latest_for_candidate.assert_not_called()


def test_queue_result_uses_only_the_session_attached_to_the_owned_item() -> None:
    service, _, queues, evaluations = _service()
    owner = HRUser(id=7, email="recruiter@example.test", password_hash="x", full_name="Recruiter")
    report = _report()
    queues.get_item.return_value = QueueItemRecord(
        id=9, queue_id=3, candidate_id=42, interview_session_id="session-1",
    )
    evaluations.get_for_session.return_value = _evaluation(report)

    result = service.for_queue_item(owner, 3, 9)

    queues.get_item.assert_called_once_with(owner, 3, 9)
    evaluations.get_for_session.assert_called_once_with("session-1")
    assert result == report


def test_an_item_without_a_completed_session_has_no_result() -> None:
    service, _, queues, evaluations = _service()
    owner = HRUser(id=7, email="recruiter@example.test", password_hash="x", full_name="Recruiter")
    queues.get_item.return_value = QueueItemRecord(id=9, queue_id=3, candidate_id=42)

    assert service.for_queue_item(owner, 3, 9) is None
    evaluations.get_for_session.assert_not_called()
