"""Shared scenario builders for the queue tests. Not a test module itself.

Queue claiming is the one thing in this codebase that CANNOT be tested through
tests/conftest.py's `db_session_factory`: that fixture wraps everything in a
single outer transaction on a single connection, so a second "worker" would never
see the first one's rows and FOR UPDATE SKIP LOCKED would be meaningless.

So these tests use autonomous session factories on real, separate connections and
clean up after themselves by deleting the position they created -- everything else
(candidates, plans, queues, queue items) cascades from it.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from models.common import (
    CandidateStatus,
    InterviewPlanStatus,
    QueueItemStatus,
    QueueKind,
    QuestionCategory,
    QueueStatus,
)
from models.platform import (
    CallQueueRecord,
    CandidateInterviewPlan,
    CandidatePlanQuestion,
    CandidateRecord,
    HRUser,
    Position,
    PositionQuestionRecord,
    QueueItemRecord,
)
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.interview_plans import SQLAlchemyInterviewPlanRepository
from services.db.orm_models import HRUserRow
from services.db.positions import SQLAlchemyPositionRepository
from services.db.questions import SQLAlchemyPositionQuestionRepository
from services.db.queues import SQLAlchemyQueueRepository

_email_counter = 0


def autonomous_factory(engine: Engine) -> sessionmaker[Session]:
    """A session factory with its own connections, committing for real.

    Required for anything involving two concurrent workers, and for the worker
    tests, where the worker's writes must be visible to the test's own reads.
    """
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def unique_email(label: str) -> str:
    global _email_counter
    _email_counter += 1
    return f"{label}-{_email_counter}@queue-tests.example"


@dataclass
class QueueScenario:
    """A complete, realistic queue: an owner, a position with questions, candidates
    with recruiter-approved interview plans, and a queue holding them."""

    factory: sessionmaker[Session]
    owner: HRUser
    position: Position
    queue: CallQueueRecord
    candidate_ids: list[int] = field(default_factory=list)
    item_ids: list[int] = field(default_factory=list)

    @property
    def queue_repo(self) -> SQLAlchemyQueueRepository:
        return SQLAlchemyQueueRepository(self.factory)

    def start(self) -> None:
        self.queue = self.queue_repo.update_queue(
            self.queue.model_copy(update={"status": QueueStatus.RUNNING}),
        )

    def pause(self) -> None:
        self.queue = self.queue_repo.update_queue(
            self.queue.model_copy(update={"status": QueueStatus.PAUSED}),
        )

    def item(self, index: int) -> QueueItemRecord:
        return self.queue_repo.get_item(self.item_ids[index])

    def cleanup(self) -> None:
        """Delete the position AND the owner it was created under.

        Candidates, plans, queues and items cascade from the position's own FK.
        The owning HRUser does NOT cascade from that (an owner outliving their
        positions is correct in the real product), so it must be deleted here
        explicitly -- this fixture is the only place that ever creates one, and
        for a long time this method deleted only the position, leaving a
        permanent orphaned HRUser row (email f"{label}-N@queue-tests.example")
        behind after every real run. That is how test data accumulated in a
        real database: not a missing skip-guard, but this leak, on a session
        that was ever pointed at one. HRUserRepository has no delete() of its
        own -- there is no real product use case for self-service account
        deletion yet, so this stays a raw, test-scoped row delete rather than
        adding that capability to the production repository interface for a
        test's benefit.
        """
        SQLAlchemyPositionRepository(self.factory).delete(self.position.id)
        with self.factory() as session:
            row = session.get(HRUserRow, self.owner.id)
            if row is not None:
                session.delete(row)
                session.commit()


def build_scenario(
    factory: sessionmaker[Session],
    *,
    label: str = "queue",
    candidates: int = 1,
    approved: bool = True,
    max_attempts: int = 3,
    enqueue: bool = True,
    running: bool = False,
    # Auto screening pipeline (see application/queue_worker.py's one worker
    # change): kind=AUTO plus item_status=AWAITING_CANDIDATE reproduces what
    # the pipeline itself creates, without going through the pipeline service.
    kind: QueueKind = QueueKind.MANUAL,
    item_status: QueueItemStatus = QueueItemStatus.PENDING,
) -> QueueScenario:
    user_repo = SQLAlchemyHRUserRepository(factory)
    position_repo = SQLAlchemyPositionRepository(factory)
    question_repo = SQLAlchemyPositionQuestionRepository(factory)
    candidate_repo = SQLAlchemyCandidateRepository(factory)
    plan_repo = SQLAlchemyInterviewPlanRepository(factory)
    queue_repo = SQLAlchemyQueueRepository(factory)

    owner = user_repo.create(HRUser(
        email=unique_email(label), password_hash="hashed-not-a-real-password", full_name="Queue Owner",
    ))
    position = position_repo.create(Position(
        owner_id=owner.id,
        company_name="FlairsTech",
        title="Junior AI Engineer",
        description="DEMO DATA — fictional position used by the queue tests.",
        experience_level="Junior",
    ))
    question_repo.add(PositionQuestionRecord(
        position_id=position.id,
        category=QuestionCategory.TECHNICAL,
        question="Walk me through a Python service you built.",
        order=0,
        purpose="Assess hands-on technical depth.",
        expected_topics=["Python"],
    ))
    queue = queue_repo.create_queue(CallQueueRecord(
        position_id=position.id,
        name=f"{label} screening queue",
        status=QueueStatus.RUNNING if running else QueueStatus.IDLE,
        kind=kind,
    ))

    scenario = QueueScenario(factory=factory, owner=owner, position=position, queue=queue)
    for index in range(candidates):
        candidate = candidate_repo.create(CandidateRecord(
            position_id=position.id,
            full_name=f"Demo Candidate {index + 1}",
            email=f"candidate-{index + 1}@queue-tests.example",
            cv_text="DEMO DATA — fictional CV. Built a Python retrieval service and wrote tests.",
            status=CandidateStatus.NEW,
        ))
        scenario.candidate_ids.append(candidate.id)
        if approved:
            approve_plan(plan_repo, candidate.id)
        if enqueue:
            item = queue_repo.add_item(QueueItemRecord(
                queue_id=queue.id, candidate_id=candidate.id, max_attempts=max_attempts,
                status=item_status,
            ))
            scenario.item_ids.append(item.id)
    return scenario


def approve_plan(
    plan_repo: SQLAlchemyInterviewPlanRepository, candidate_id: int, *, approved: bool = True,
) -> CandidateInterviewPlan:
    """Store the recruiter-approved plan a queued candidate must have."""
    now = datetime.now(timezone.utc)
    return plan_repo.upsert(CandidateInterviewPlan(
        candidate_id=candidate_id,
        status=InterviewPlanStatus.APPROVED if approved else InterviewPlanStatus.DRAFT,
        questions=[
            CandidatePlanQuestion(
                category=QuestionCategory.INTRODUCTION,
                question="Tell me about your background.",
                order=0,
                purpose="Open the conversation.",
            ),
            CandidatePlanQuestion(
                category=QuestionCategory.TECHNICAL,
                question="Walk me through a Python service you built.",
                order=1,
                purpose="Assess hands-on technical depth.",
                expected_topics=["Python"],
            ),
        ],
        generated_at=now,
        approved_at=now if approved else None,
    ))
