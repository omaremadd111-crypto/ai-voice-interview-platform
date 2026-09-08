"""The P5 demo, run for real: a queue of three candidates screened by the worker.

Queue -> candidate -> recruiter-approved plan -> simulated call -> completion ->
next candidate, on a real PostgreSQL database, driving the real
InterviewAgentService through the null transport. Nothing is stubbed except the
conversation channel itself, which is the one thing P5 is explicitly not building.

The three demo candidates answer from sample_data/answers_*.json (the same
fictional scripts Phase 8's demo uses), so the run exercises the real evaluation
path end to end rather than filler text.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from application.interview_agent_service import InterviewAgentService
from application.interview_preparation_service import InterviewPreparationService
from application.position_service import PositionService
from application.queue_service import QueueService
from application.queue_worker import QueueWorker, RetryPolicy, WorkerSettings
from config.settings import Settings
from models.common import (
    CandidateStatus,
    InterviewPlanStatus,
    QueueItemStatus,
    QueueStatus,
    QuestionCategory,
)
from models.platform import (
    CandidateInterviewPlan,
    CandidatePlanQuestion,
    CandidateRecord,
    HRUser,
    Position,
    PositionQuestionRecord,
)
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.evaluations import SQLAlchemyEvaluationRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.interview_plans import SQLAlchemyInterviewPlanRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.postgres_session_store import PostgresSessionStore
from services.db.questions import SQLAlchemyPositionQuestionRepository
from services.db.queues import SQLAlchemyQueueRepository
from services.interview_transport import (
    SIMULATED_ANSWER_MARKER,
    NullInterviewTransport,
    scripted_answers,
)
from tests.db.queue_fixtures import autonomous_factory, unique_email

PROFILES = ("strong", "average", "weak")

DEMO_QUESTIONS = [
    (QuestionCategory.INTRODUCTION, "Tell me about yourself and why this role interests you."),
    (QuestionCategory.CANDIDATE_BACKGROUND, "Walk me through your relevant experience."),
    (QuestionCategory.TECHNICAL, "How would you build a retrieval pipeline in Python?"),
    (QuestionCategory.PROBLEM_SOLVING, "How do you approach a bug you cannot reproduce?"),
    (QuestionCategory.BEHAVIORAL, "Tell me about a time you disagreed with a teammate."),
]

# Words a recruiter-facing artefact must never contain (docs/ARCHITECTURE.md).
FORBIDDEN_WORDS = ("hire", "reject", "disqualified")


@pytest.fixture()
def factory(pg_engine: Engine) -> sessionmaker[Session]:
    return autonomous_factory(pg_engine)


@pytest.fixture()
def demo(factory: sessionmaker[Session], project_root: Path) -> Iterator[dict]:
    """A recruiter with a position and three approved candidates, sitting in a queue."""
    user_repo = SQLAlchemyHRUserRepository(factory)
    position_repo = SQLAlchemyPositionRepository(factory)
    question_repo = SQLAlchemyPositionQuestionRepository(factory)
    candidate_repo = SQLAlchemyCandidateRepository(factory)
    plan_repo = SQLAlchemyInterviewPlanRepository(factory)

    owner = user_repo.create(HRUser(
        email=unique_email("demo-recruiter"), password_hash="hashed", full_name="Demo Recruiter",
    ))
    position = position_repo.create(Position(
        owner_id=owner.id,
        company_name="FlairsTech",
        title="Junior AI Engineer",
        description=(project_root / "sample_data" / "flairstech_junior_ai_engineer_jd.md").read_text(
            encoding="utf-8",
        ),
        experience_level="Junior",
    ))
    for order, (category, text) in enumerate(DEMO_QUESTIONS):
        question_repo.add(PositionQuestionRecord(
            position_id=position.id, category=category, question=text, order=order,
            purpose="Baseline question for the role.",
        ))

    now = datetime.now(timezone.utc)
    candidate_ids: dict[str, int] = {}
    for profile in PROFILES:
        cv = (project_root / "sample_data" / f"cv_{profile}.md").read_text(encoding="utf-8")
        candidate = candidate_repo.create(CandidateRecord(
            position_id=position.id,
            full_name=f"Demo Candidate — {profile.title()} Profile",
            email=f"{profile}@demo.example",
            cv_text=cv,
        ))
        # The recruiter reviews and approves each plan before anyone is queued.
        plan_repo.upsert(CandidateInterviewPlan(
            candidate_id=candidate.id,
            status=InterviewPlanStatus.APPROVED,
            questions=[
                CandidatePlanQuestion(
                    category=category, question=text, order=order,
                    purpose="Baseline question for the role.",
                )
                for order, (category, text) in enumerate(DEMO_QUESTIONS)
            ],
            generated_at=now,
            approved_at=now,
        ))
        candidate_ids[profile] = candidate.id

    yield {"owner": owner, "position": position, "candidate_ids": candidate_ids}
    position_repo.delete(position.id)


def build_services(factory: sessionmaker[Session], reports_dir: Path, answers: dict[str, str]):
    settings = Settings(mock_mode=True, reports_dir=reports_dir)
    position_service = PositionService(SQLAlchemyPositionRepository(factory))
    candidate_repo = SQLAlchemyCandidateRepository(factory)
    plan_repo = SQLAlchemyInterviewPlanRepository(factory)
    agent_service = InterviewAgentService(
        settings=settings, session_store=PostgresSessionStore(factory),
    )
    queue_service = QueueService(
        SQLAlchemyQueueRepository(factory), position_service, candidate_repo, plan_repo,
    )
    worker = QueueWorker(
        queue_repo=SQLAlchemyQueueRepository(factory),
        candidate_repo=candidate_repo,
        hr_user_repo=SQLAlchemyHRUserRepository(factory),
        position_repo=SQLAlchemyPositionRepository(factory),
        plan_repo=plan_repo,
        preparation_service=InterviewPreparationService(
            position_service,
            SQLAlchemyPositionQuestionRepository(factory),
            candidate_repo,
            agent_service,
            plan_repo,
        ),
        agent_service=agent_service,
        transport=NullInterviewTransport(scripted_answers(answers)),
        settings=WorkerSettings(
            worker_id="demo-worker", retry_policy=RetryPolicy(base_seconds=1, max_seconds=4),
        ),
    )
    return queue_service, worker, agent_service


def load_answers(project_root: Path) -> dict[str, str]:
    """One combined answer script. All three demo CVs share the question set, so
    the mock's CV-driven heuristics -- not the script -- are what make the three
    candidates diverge."""
    script = json.loads(
        (project_root / "sample_data" / "answers_average.json").read_text(encoding="utf-8"),
    )
    return script["answers"]


def test_the_worker_screens_a_whole_queue_from_start_to_finish(
    factory: sessionmaker[Session], demo: dict, project_root: Path, tmp_path: Path,
) -> None:
    queue_service, worker, agent_service = build_services(
        factory, tmp_path, load_answers(project_root),
    )
    owner = demo["owner"]

    # 1. The recruiter builds the queue.
    queue = queue_service.create(owner, demo["position"].id, name="Junior AI Engineer — round 1")
    for profile in PROFILES:
        queue_service.add_candidate(owner, queue.id, demo["candidate_ids"][profile])

    # 2. Nothing happens until they start it -- the worker is already running.
    assert worker.run_until_idle() == []
    assert queue_service.progress(owner, queue.id).counts[QueueItemStatus.PENDING] == 3

    # 3. Start: the worker drains the queue, one candidate at a time.
    queue_service.start(owner, queue.id)
    processed = worker.run_until_idle()

    assert len(processed) == 3
    assert [p.status for p in processed] == [QueueItemStatus.COMPLETED] * 3
    assert [p.candidate_id for p in processed] == [
        demo["candidate_ids"][profile] for profile in PROFILES
    ], "candidates must be screened in the order they were queued"

    # 4. Every candidate has a real, evaluated interview on file.
    for result in processed:
        assert result.session_id is not None
        status = agent_service.get_status(result.session_id)
        assert status.has_evaluation and status.has_report
        assert status.total_turns >= len(DEMO_QUESTIONS)
        persisted = SQLAlchemyEvaluationRepository(factory).get_latest_for_candidate(
            result.candidate_id,
        )
        assert persisted is not None
        assert persisted.interview_session_id == result.session_id
        assert persisted.report is not None
        assert persisted.report.full_transcript

    progress = queue_service.progress(owner, queue.id)
    assert progress.total == 3
    assert progress.counts[QueueItemStatus.COMPLETED] == 3
    assert progress.finished == 3
    assert progress.in_flight == 0

    # 5. And the queue is genuinely empty of work.
    assert worker.run_until_idle() == []


def test_pausing_mid_queue_stops_after_the_current_candidate(
    factory: sessionmaker[Session], demo: dict, project_root: Path, tmp_path: Path,
) -> None:
    queue_service, worker, _ = build_services(factory, tmp_path, load_answers(project_root))
    owner = demo["owner"]
    queue = queue_service.create(owner, demo["position"].id, name="Pause demo")
    for profile in PROFILES:
        queue_service.add_candidate(owner, queue.id, demo["candidate_ids"][profile])

    queue_service.start(owner, queue.id)
    first = worker.run_until_idle(max_items=1)
    assert len(first) == 1

    queue_service.pause(owner, queue.id)
    assert worker.run_until_idle() == []
    paused = queue_service.progress(owner, queue.id)
    assert paused.status is QueueStatus.PAUSED
    assert paused.counts[QueueItemStatus.COMPLETED] == 1
    assert paused.counts[QueueItemStatus.PENDING] == 2

    queue_service.resume(owner, queue.id)
    assert len(worker.run_until_idle()) == 2
    assert queue_service.progress(owner, queue.id).finished == 3


def test_the_demo_run_respects_the_hiring_safety_rules(
    factory: sessionmaker[Session], demo: dict, project_root: Path, tmp_path: Path,
) -> None:
    """A full queue run must not produce a hiring decision anywhere, and its
    transcripts must be unmistakably marked as simulated."""
    queue_service, worker, agent_service = build_services(
        factory, tmp_path, load_answers(project_root),
    )
    owner = demo["owner"]
    queue = queue_service.create(owner, demo["position"].id, name="Safety demo")
    queue_service.add_candidate(owner, queue.id, demo["candidate_ids"]["strong"])
    queue_service.start(owner, queue.id)

    processed = worker.run_until_idle()
    assert len(processed) == 1
    report = agent_service.generate_report(processed[0].session_id)

    rendered = report.model_dump_json().lower()
    for word in FORBIDDEN_WORDS:
        assert word not in rendered, f"the report contains the forbidden word '{word}'"

    # The recommendation is one of the four allowed labels, computed in Python.
    assert report.recommendation.value in {
        "Strong evidence for human review",
        "Proceed to deeper technical assessment",
        "Requires additional validation",
        "Insufficient evidence from this interview",
    }

    # Nothing produced by the null transport can be mistaken for a real answer.
    written = list(tmp_path.glob("*.md"))
    assert written, "no report was written"
    for path in written:
        assert SIMULATED_ANSWER_MARKER in path.read_text(encoding="utf-8")


def test_the_screening_outcome_is_recomputed_identically_on_a_second_run(
    factory: sessionmaker[Session], demo: dict, project_root: Path, tmp_path: Path,
) -> None:
    """Determinism through the whole queue path: the same candidate, the same
    approved plan, and the same scripted answers must score identically."""
    answers = load_answers(project_root)
    evaluation_repo = SQLAlchemyEvaluationRepository(factory)
    outcomes = []
    for run in range(2):
        queue_service, worker, _ = build_services(factory, tmp_path, answers)
        owner = demo["owner"]
        queue = queue_service.create(owner, demo["position"].id, name=f"Determinism run {run}")
        queue_service.add_candidate(owner, queue.id, demo["candidate_ids"]["average"])
        queue_service.start(owner, queue.id)

        processed = worker.run_until_idle()
        assert len(processed) == 1
        # Read what was actually persisted, not what the in-process service holds.
        evaluation = evaluation_repo.get_for_session(processed[0].session_id)
        assert evaluation is not None
        outcomes.append((
            evaluation.overall_score,
            evaluation.screening_outcome,
            evaluation.recommendation,
        ))

    assert outcomes[0] == outcomes[1]


def test_a_worker_restart_resumes_a_half_finished_queue(
    factory: sessionmaker[Session], demo: dict, project_root: Path, tmp_path: Path,
) -> None:
    """The reliability claim: the queue lives in the database, not in a process.
    A fresh worker picks up exactly where the old one stopped."""
    answers = load_answers(project_root)
    queue_service, first_worker, _ = build_services(factory, tmp_path, answers)
    owner = demo["owner"]
    queue = queue_service.create(owner, demo["position"].id, name="Restart demo")
    for profile in PROFILES:
        queue_service.add_candidate(owner, queue.id, demo["candidate_ids"][profile])
    queue_service.start(owner, queue.id)

    first_worker.run_until_idle(max_items=1)
    del first_worker  # the process "dies"

    _, second_worker, _ = build_services(factory, tmp_path, answers)
    remaining = second_worker.run_until_idle()

    assert len(remaining) == 2
    assert queue_service.progress(owner, queue.id).counts[QueueItemStatus.COMPLETED] == 3


def test_a_lease_left_behind_by_a_dead_worker_is_recovered(
    factory: sessionmaker[Session], demo: dict, project_root: Path, tmp_path: Path,
) -> None:
    queue_service, worker, _ = build_services(factory, tmp_path, load_answers(project_root))
    owner = demo["owner"]
    queue = queue_service.create(owner, demo["position"].id, name="Recovery demo")
    queue_service.add_candidate(owner, queue.id, demo["candidate_ids"]["weak"])
    queue_service.start(owner, queue.id)

    queue_repo = SQLAlchemyQueueRepository(factory)
    now = datetime.now(timezone.utc)
    stranded = queue_repo.claim_next_item(
        worker_id="dead-worker",
        now=now,
        lease_expires_at=now - timedelta(seconds=1),  # already expired
        queue_id=queue.id,
    )
    assert stranded is not None

    processed = worker.run_until_idle()
    assert len(processed) == 1
    assert processed[0].item_id == stranded.id
    assert processed[0].status is QueueItemStatus.COMPLETED

    candidate = SQLAlchemyCandidateRepository(factory).get(demo["candidate_ids"]["weak"])
    assert candidate.status is CandidateStatus.SCREENED
