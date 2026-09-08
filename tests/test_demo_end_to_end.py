"""Phase 8: three fictional demo candidates through the real application pipeline."""
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from application.dto import PrepareInterviewRequest
from application.interview_agent_service import InterviewAgentService
from config.settings import Settings
from models.evaluation import HRReport, InterviewEvaluation
from services.session_service import InMemorySessionStore

DEMO_NOTICE = (
    "DEMO DATA — fictional content created for this prototype. Not an actual FlairsTech "
    "vacancy, requirement, candidate, or process."
)
PROFILE_ORDER = ("strong", "average", "weak")


class DemoOutcome(BaseModel):
    profile: str
    session_id: str
    evaluation: InterviewEvaluation
    report: HRReport


def _sample_path(project_root: Path, filename: str) -> Path:
    return project_root / "sample_data" / filename


def _load_script(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_candidate(
    service: InterviewAgentService,
    reports_dir: Path,
    jd_path: Path,
    cv_path: Path,
    answers_path: Path,
    profile: str,
) -> DemoOutcome:
    script = _load_script(answers_path)
    answers: dict[str, str] = script["answers"]
    prepared = service.prepare_interview(PrepareInterviewRequest(
        company_name="FlairsTech",
        job_title="Junior AI Engineer",
        experience_level="Junior",
        job_description_path=jd_path,
        candidate_name=f"Demo Candidate — {profile.title()} Profile",
        candidate_cv_path=cv_path,
        num_questions=7,
        approximate_duration_minutes=25,
    ))
    service.approve_plan(prepared.session_id)
    prompt = service.start_interview(prepared.session_id)
    safety_counter = 0
    while not prompt.finished:
        key = "follow_up" if prompt.is_follow_up else prompt.category.value
        answer = answers.get(key, answers["fallback"])
        prompt = service.submit_answer(prepared.session_id, answer)
        safety_counter += 1
        assert safety_counter < 50

    evaluation = service.evaluate_interview(prepared.session_id)
    report = service.generate_report(prepared.session_id)
    assert (reports_dir / f"{prepared.session_id}.md").exists()
    return DemoOutcome(
        profile=profile,
        session_id=prepared.session_id,
        evaluation=evaluation,
        report=report,
    )


@pytest.fixture(scope="module")
def demo_suite(
    project_root: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[dict[str, DemoOutcome], InMemorySessionStore]:
    reports_dir = tmp_path_factory.mktemp("phase8-reports")
    store = InMemorySessionStore()
    service = InterviewAgentService(
        settings=Settings(mock_mode=True, reports_dir=reports_dir),
        session_store=store,
    )
    jd_path = _sample_path(project_root, "flairstech_junior_ai_engineer_jd.md")
    outcomes = {
        profile: _run_candidate(
            service,
            reports_dir,
            jd_path,
            _sample_path(project_root, f"cv_{profile}.md"),
            _sample_path(project_root, f"answers_{profile}.json"),
            profile,
        )
        for profile in PROFILE_ORDER
    }
    return outcomes, store


def test_every_demo_file_is_clearly_labelled_as_fictional(project_root: Path) -> None:
    markdown_files = [
        "flairstech_junior_ai_engineer_jd.md",
        "cv_strong.md",
        "cv_average.md",
        "cv_weak.md",
    ]
    json_files = ["answers_strong.json", "answers_average.json", "answers_weak.json"]

    for filename in markdown_files:
        text = _sample_path(project_root, filename).read_text(encoding="utf-8")
        assert text.startswith(DEMO_NOTICE), f"{filename} does not open with the required notice"
    for filename in json_files:
        payload = _load_script(_sample_path(project_root, filename))
        assert payload["demo_notice"] == DEMO_NOTICE


def test_demo_scores_are_meaningfully_ordered(
    demo_suite: tuple[dict[str, DemoOutcome], InMemorySessionStore],
) -> None:
    outcomes, _store = demo_suite
    strong_score = outcomes["strong"].evaluation.overall_score
    average_score = outcomes["average"].evaluation.overall_score
    weak_score = outcomes["weak"].evaluation.overall_score

    assert strong_score is not None
    assert average_score is not None
    assert weak_score is not None
    assert strong_score > average_score > weak_score
    assert strong_score - average_score >= 10
    assert average_score - weak_score >= 10


def test_every_scored_claim_has_real_transcript_evidence(
    demo_suite: tuple[dict[str, DemoOutcome], InMemorySessionStore],
) -> None:
    outcomes, _store = demo_suite
    for outcome in outcomes.values():
        transcript_answers = [turn.answer for turn in outcome.report.full_transcript]
        assert transcript_answers
        for category in outcome.evaluation.category_evaluations:
            if category.score is None:
                assert category.sufficient_evidence is False
                assert category.reasoning == "Insufficient evidence"
                continue
            assert category.sufficient_evidence is True
            assert category.evidence
            for evidence in category.evidence:
                traceable = evidence[:-1] if evidence.endswith("…") else evidence
                assert any(traceable in answer for answer in transcript_answers)


def test_weak_profile_has_weaker_coverage_and_more_validation_areas(
    demo_suite: tuple[dict[str, DemoOutcome], InMemorySessionStore],
) -> None:
    outcomes, _store = demo_suite
    strong = outcomes["strong"]
    average = outcomes["average"]
    weak = outcomes["weak"]

    assert strong.evaluation.evidence_coverage > weak.evaluation.evidence_coverage
    assert average.evaluation.evidence_coverage > weak.evaluation.evidence_coverage
    assert len(weak.report.areas_requiring_validation) > len(average.report.areas_requiring_validation)
    assert len(weak.report.areas_requiring_validation) > len(strong.report.areas_requiring_validation)


def test_demo_sessions_and_reports_remain_isolated(
    demo_suite: tuple[dict[str, DemoOutcome], InMemorySessionStore],
) -> None:
    outcomes, store = demo_suite
    session_ids = {outcome.session_id for outcome in outcomes.values()}
    assert len(session_ids) == 3

    unique_markers = {"strong": "Atlas", "average": "Orchid", "weak": "online example"}
    for profile, outcome in outcomes.items():
        session = store.get(outcome.session_id)
        own_marker = unique_markers[profile]
        other_markers = {marker for name, marker in unique_markers.items() if name != profile}
        assert own_marker in session.candidate_input.cv_text or any(
            own_marker in turn.answer for turn in session.transcript.turns
        )
        assert all(marker not in session.candidate_input.cv_text for marker in other_markers)
        assert all(
            marker not in turn.answer
            for marker in other_markers
            for turn in outcome.report.full_transcript
        )

        original_cv_text = session.candidate_input.cv_text
        detached = store.get(outcome.session_id)
        detached.candidate_input.cv_text = "Detached mutation"
        assert store.get(outcome.session_id).candidate_input.cv_text == original_cv_text
