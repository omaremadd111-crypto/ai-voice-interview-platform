"""Phase 9 thin-UI construction and adapter tests."""
import ast
import json
from pathlib import Path
from typing import Any

import pytest

from application.interview_agent_service import InterviewAgentService
from config.settings import Settings
from models.common import InterviewState
from ui.gradio_app import (
    add_plan_row,
    _prepare_request,
    approve_plan_view,
    create_gradio_app,
    end_interview_view,
    generate_report_view,
    prepare_interview_view,
    remove_plan_row,
    save_plan_view,
    start_interview_view,
    submit_answer_view,
)

JD_TEXT = (
    "Junior AI Engineer needed to build Python RAG APIs with SQL, retrieval, vector "
    "databases, testing, Docker, evaluation, and stakeholder communication."
)
CV_TEXT = (
    "Built and tested a Python RAG API using SQL, retrieval, a vector database, and "
    "Docker. Reduced latency by 30 percent and explained results to stakeholders."
)
ANSWER = (
    "I built a Python RAG API using SQL, retrieval, a vector database, tests, and "
    "Docker because reliability mattered. I measured a 30 percent latency improvement."
)


class ProgressRecorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, _value: Any, *, desc: str) -> None:
        self.messages.append(desc)


def test_gradio_app_has_three_stages_mock_banner_and_one_state(tmp_path: Path) -> None:
    settings = Settings(mock_mode=True, reports_dir=tmp_path / "reports")
    service = InterviewAgentService(settings=settings)

    app = create_gradio_app(service, settings.reports_dir, is_mock=True)
    config = app.get_config_file()
    serialized = json.dumps(config, default=str, ensure_ascii=False)
    states = [component for component in config["components"] if component["type"] == "state"]

    assert len(states) == 1
    assert "1 · HR Setup" in serialized
    assert "2 · Candidate Interview" in serialized
    assert "3 · HR Report" in serialized
    assert "Demo / Mock Mode" in serialized
    assert "deterministic local mock, not an LLM" in serialized


def test_ui_adapters_complete_prepare_interview_and_report_flow(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    cv_path = tmp_path / "candidate.txt"
    cv_path.write_text(CV_TEXT, encoding="utf-8")
    service = InterviewAgentService(
        settings=Settings(mock_mode=True, reports_dir=reports_dir),
    )
    progress = ProgressRecorder()

    session_id, rows, plan_status, header, progress_log = prepare_interview_view(
        service, "FlairsTech", "Junior AI Engineer", "Junior", JD_TEXT, None,
        str(cv_path), 4, 20, progress,
    )
    assert len(rows) == 4
    assert "Plan ready" in plan_status
    assert header == "### FlairsTech · Junior AI Engineer"
    assert progress.messages[-1] == "Interview ready."
    assert progress_log.endswith("Interview ready.")

    added_rows, _message = add_plan_row(rows)
    assert len(added_rows) == 5
    restored_rows, _message = remove_plan_row(added_rows, 5)
    restored_rows[0][2] = "Describe your most relevant AI project."
    saved_rows, _message = save_plan_view(service, session_id, restored_rows)
    assert saved_rows[0][2] == "Describe your most relevant AI project."
    _approved_rows, approval, state_line = approve_plan_view(service, session_id, saved_rows)
    assert "approved" in approval.lower()
    assert "Ready" in state_line

    question, interview_progress, state_line, cleared_answer = start_interview_view(service, session_id)
    assert "Question 1 of 4" in question
    assert "score" not in question.lower()
    assert "evidence" not in question.lower()
    assert "rubric" not in question.lower()
    assert "0% complete" in interview_progress
    assert "In Progress" in state_line
    assert cleared_answer == ""

    safety_counter = 0
    while service.get_status(session_id).answered_main_questions < 3:
        _next_question, _progress, _state_line, cleared_answer = submit_answer_view(
            service, session_id, ANSWER,
        )
        assert cleared_answer == ""
        safety_counter += 1
        assert safety_counter < 20
    end_question, end_progress, end_state, _answer = end_interview_view(service, session_id)
    assert end_question == "Interview ended. Thank you."
    assert "main questions answered" in end_progress
    assert "Completed" in end_state

    report_view = generate_report_view(service, reports_dir, session_id)
    overall, coverage, categories = report_view[:3]
    strengths = report_view[3]
    validation_areas = report_view[4]
    evidence = report_view[5]
    follow_ups = report_view[6]
    transcript = report_view[7]
    recommendation = report_view[8]
    summary = report_view[9]
    report_path = Path(report_view[10])
    assert overall is not None
    assert coverage.endswith("%")
    assert categories
    assert strengths.startswith("### Strengths")
    assert validation_areas.startswith("### Areas requiring validation")
    assert evidence.startswith("### Strong evidence")
    assert follow_ups.startswith("### Human follow-up questions")
    assert transcript.startswith("### Full transcript")
    assert ANSWER in transcript
    assert recommendation
    assert summary.startswith("### Interview summary")
    assert report_path.exists()
    assert service.get_status(session_id).state == InterviewState.EVALUATED


def test_prepare_request_requires_candidate_cv() -> None:
    with pytest.raises(ValueError, match="Candidate CV upload is required"):
        _prepare_request(
            "FlairsTech", "Junior AI Engineer", "Junior", JD_TEXT,
            None, None, 4, 20,
        )


def test_gradio_imports_are_confined_to_ui(project_root: Path) -> None:
    violations: list[str] = []
    for path in project_root.rglob("*.py"):
        if "ui" in path.relative_to(project_root).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(alias.name == "gradio" for alias in node.names):
                violations.append(str(path.relative_to(project_root)))
            if isinstance(node, ast.ImportFrom) and node.module == "gradio":
                violations.append(str(path.relative_to(project_root)))

    assert violations == []
