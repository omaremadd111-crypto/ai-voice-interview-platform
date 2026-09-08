"""Thin Gradio presentation adapter for the interview application service."""
from __future__ import annotations

from collections.abc import Callable
from functools import partial, wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeAlias, TypeVar

import gradio as gr
from pydantic import ValidationError

from application.dto import PrepareInterviewRequest
from application.interview_agent_service import InterviewAgentService
from models.common import ExperienceLevel, InterviewState
from models.evaluation import HRReport
from models.interview import InterviewPlan, InterviewQuestion, NextPrompt
from services.document_parser import DocumentParseError
from services.interview_engine import InvalidInterviewStateError
from services.llm.base import LLMError
from services.report_service import ReportSafetyError
from services.session_service import SessionStoreError

PlanRow: TypeAlias = list[Any]
PlanRows: TypeAlias = list[PlanRow]
PrepareView: TypeAlias = tuple[str, PlanRows, str, str, str]
PromptView: TypeAlias = tuple[str, str, str, str]
ReportView: TypeAlias = tuple[
    str,
    str,
    PlanRows,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
]

_P = ParamSpec("_P")
_R = TypeVar("_R")
_UI_EXCEPTIONS: tuple[type[Exception], ...] = (
    DocumentParseError,
    InvalidInterviewStateError,
    LLMError,
    ReportSafetyError,
    SessionStoreError,
    ValidationError,
    OSError,
    ValueError,
)

PLAN_HEADERS = [
    "Question ID",
    "Category",
    "Question",
    "Purpose",
    "Expected topics (semicolon-separated)",
    "Difficulty",
    "Follow-ups allowed",
]


def ui_errors(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Translate known application failures into safe, readable UI errors."""

    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return function(*args, **kwargs)
        except _UI_EXCEPTIONS as exc:
            raise gr.Error(str(exc)) from exc

    return wrapped


@ui_errors
def prepare_interview_view(
    service: InterviewAgentService,
    company_name: str,
    job_title: str,
    experience_level: str | None,
    job_description_text: str,
    job_description_file: str | None,
    candidate_cv_file: str | None,
    num_questions: int | float,
    approximate_duration: int | float,
    progress: gr.Progress = gr.Progress(),
) -> PrepareView:
    progress_messages: list[str] = []
    request = _prepare_request(
        company_name, job_title, experience_level, job_description_text,
        job_description_file, candidate_cv_file, num_questions, approximate_duration,
    )
    result = service.prepare_interview(
        request,
        on_progress=_progress_callback(progress, progress_messages),
    )
    status = f"Plan ready for HR review · {len(result.interview_plan.questions)} questions"
    header = f"### {company_name.strip()} · {job_title.strip()}"
    return result.session_id, plan_to_rows(result.interview_plan), status, header, "\n".join(progress_messages)


@ui_errors
def save_plan_view(
    service: InterviewAgentService,
    session_id: str,
    rows: PlanRows | None,
) -> tuple[PlanRows, str]:
    plan = service.update_plan(session_id, rows_to_questions(rows))
    return plan_to_rows(plan), f"Saved {len(plan.questions)} interview questions."


@ui_errors
def approve_plan_view(
    service: InterviewAgentService,
    session_id: str,
    rows: PlanRows | None,
) -> tuple[PlanRows, str, str]:
    plan = service.update_plan(session_id, rows_to_questions(rows))
    service.approve_plan(session_id)
    status = service.get_status(session_id)
    return plan_to_rows(plan), "Plan approved. Candidate interview is ready.", _status_line(status.state.value)


def add_plan_row(rows: PlanRows | None) -> tuple[PlanRows, str]:
    normalized_rows = _normalize_rows(rows)
    question_id = _next_custom_question_id(normalized_rows)
    normalized_rows.append([
        question_id, "behavioral", "New HR question", "HR-added validation question",
        "", "medium", True,
    ])
    return normalized_rows, "Question added. Edit the row, then save or approve the plan."


@ui_errors
def remove_plan_row(rows: PlanRows | None, row_number: int | float) -> tuple[PlanRows, str]:
    normalized_rows = _normalize_rows(rows)
    index = int(row_number) - 1
    if index < 0 or index >= len(normalized_rows):
        raise ValueError(f"Row number must be between 1 and {len(normalized_rows)}.")
    normalized_rows.pop(index)
    return normalized_rows, f"Removed row {index + 1}. Save or approve to persist the change."


@ui_errors
def start_interview_view(service: InterviewAgentService, session_id: str) -> PromptView:
    prompt = service.start_interview(session_id)
    status = service.get_status(session_id)
    return _prompt_view(prompt, status.company, status.role)


@ui_errors
def submit_answer_view(
    service: InterviewAgentService,
    session_id: str,
    answer: str,
) -> PromptView:
    prompt = service.submit_answer(session_id, answer)
    status = service.get_status(session_id)
    question, progress, notice, _unused = _prompt_view(prompt, status.company, status.role)
    return question, progress, notice, ""


@ui_errors
def end_interview_view(service: InterviewAgentService, session_id: str) -> PromptView:
    status = service.get_status(session_id)
    if status.state == InterviewState.IN_PROGRESS:
        service.end_interview(session_id)
        status = service.get_status(session_id)
    elif status.state != InterviewState.COMPLETED:
        raise InvalidInterviewStateError(f"Cannot end an interview from state {status.state}.")
    progress = f"Completed · {status.answered_main_questions} of {status.total_questions} main questions answered"
    return "Interview ended. Thank you.", progress, _status_line(status.state.value), ""


@ui_errors
def generate_report_view(
    service: InterviewAgentService,
    reports_dir: Path,
    session_id: str,
) -> ReportView:
    service.evaluate_interview(session_id)
    report = service.generate_report(session_id)
    report_path = reports_dir / f"{session_id}.md"
    return report_to_view(report, report_path)


def create_gradio_app(
    service: InterviewAgentService,
    reports_dir: Path,
    *,
    is_mock: bool,
) -> gr.Blocks:
    """Build the UI while keeping all interview business state in the service."""

    with gr.Blocks(title="AI Candidate Interview Agent") as app:
        session_id = gr.State("")
        gr.Markdown("# AI Candidate Interview Agent")
        gr.Markdown(
            "Structured, evidence-based interview support. Final decisions remain with human reviewers."
        )
        gr.Markdown(
            "## Demo / Mock Mode\nResponses come from a deterministic local mock, not an LLM.",
            visible=is_mock,
            elem_id="mock-mode-banner",
        )

        with gr.Tabs():
            with gr.Tab("1 · HR Setup"):
                gr.Markdown("## Configure and review the interview")
                with gr.Row():
                    company_name = gr.Textbox(label="Company name", value="FlairsTech")
                    job_title = gr.Textbox(label="Job title", value="Junior AI Engineer")
                    experience_level = gr.Dropdown(
                        label="Experience level",
                        choices=[level.value for level in ExperienceLevel],
                        value=ExperienceLevel.JUNIOR.value,
                    )
                with gr.Row():
                    job_description_text = gr.Textbox(
                        label="Job Description text",
                        lines=10,
                        placeholder="Paste the Job Description, or upload a file.",
                    )
                    with gr.Column():
                        job_description_file = gr.File(
                            label="Job Description file",
                            file_types=[".pdf", ".docx", ".txt", ".md"],
                            type="filepath",
                        )
                        candidate_cv_file = gr.File(
                            label="Candidate CV file",
                            file_types=[".pdf", ".docx", ".txt", ".md"],
                            type="filepath",
                        )
                with gr.Row():
                    num_questions = gr.Slider(
                        minimum=3, maximum=15, step=1, value=7,
                        label="Number of questions",
                    )
                    approximate_duration = gr.Slider(
                        minimum=10, maximum=90, step=5, value=25,
                        label="Approximate duration (minutes)",
                    )
                prepare_button = gr.Button("Prepare Interview", variant="primary")
                progress_log = gr.Textbox(label="Preparation progress", lines=7, interactive=False)
                plan_status = gr.Markdown("No interview has been prepared yet.")
                plan_table = gr.Dataframe(
                    headers=PLAN_HEADERS,
                    value=[],
                    datatype=["str", "str", "str", "str", "str", "str", "bool"],
                    type="array",
                    interactive=True,
                    row_count=1,
                    column_count=7,
                    label="Generated interview plan · editable by HR",
                    wrap=True,
                )
                with gr.Row():
                    add_question_button = gr.Button("Add Question")
                    row_to_remove = gr.Number(label="Row to remove (1-based)", value=1, precision=0)
                    remove_question_button = gr.Button("Remove Question")
                with gr.Row():
                    save_plan_button = gr.Button("Save Plan")
                    approve_plan_button = gr.Button("Approve Plan", variant="primary")

            with gr.Tab("2 · Candidate Interview"):
                candidate_header = gr.Markdown("### Interview not prepared")
                interview_progress = gr.Markdown("Not started")
                current_question = gr.Textbox(
                    label="Current question",
                    lines=5,
                    interactive=False,
                )
                answer = gr.Textbox(
                    label="Your answer",
                    lines=8,
                    placeholder="Type your answer here.",
                )
                with gr.Row():
                    start_button = gr.Button("Start Interview", variant="primary")
                    submit_button = gr.Button("Submit Answer", variant="primary")
                    end_button = gr.Button("End Interview", variant="stop")
                interview_status = gr.Markdown("The HR plan must be approved before starting.")

            with gr.Tab("3 · HR Report"):
                gr.Markdown("## Evidence-based HR report")
                generate_report_button = gr.Button("Evaluate & Generate Report", variant="primary")
                with gr.Row():
                    overall_score = gr.Textbox(label="Overall score", interactive=False)
                    evidence_coverage = gr.Textbox(label="Evidence coverage", interactive=False)
                    recommendation = gr.Textbox(label="Recommendation", interactive=False)
                category_scores = gr.Dataframe(
                    headers=["Category", "Score", "Reasoning", "Evidence", "Areas to validate"],
                    type="array",
                    datatype=["str", "str", "str", "str", "str"],
                    interactive=False,
                    label="Category scores",
                    wrap=True,
                )
                with gr.Row():
                    strengths = gr.Markdown(label="Strengths")
                    areas_to_validate = gr.Markdown(label="Areas to validate")
                evidence = gr.Markdown(label="Evidence")
                human_follow_ups = gr.Markdown(label="Human follow-up questions")
                transcript = gr.Markdown(label="Full transcript")
                report_summary = gr.Markdown(label="Interview summary")
                report_download = gr.File(label="Download markdown report", interactive=False)

        prepare_button.click(
            fn=partial(prepare_interview_view, service),
            inputs=[
                company_name, job_title, experience_level, job_description_text,
                job_description_file, candidate_cv_file, num_questions, approximate_duration,
            ],
            outputs=[session_id, plan_table, plan_status, candidate_header, progress_log],
        )
        add_question_button.click(
            fn=add_plan_row,
            inputs=[plan_table],
            outputs=[plan_table, plan_status],
        )
        remove_question_button.click(
            fn=remove_plan_row,
            inputs=[plan_table, row_to_remove],
            outputs=[plan_table, plan_status],
        )
        save_plan_button.click(
            fn=partial(save_plan_view, service),
            inputs=[session_id, plan_table],
            outputs=[plan_table, plan_status],
        )
        approve_plan_button.click(
            fn=partial(approve_plan_view, service),
            inputs=[session_id, plan_table],
            outputs=[plan_table, plan_status, interview_status],
        )
        start_button.click(
            fn=partial(start_interview_view, service),
            inputs=[session_id],
            outputs=[current_question, interview_progress, interview_status, answer],
        )
        submit_button.click(
            fn=partial(submit_answer_view, service),
            inputs=[session_id, answer],
            outputs=[current_question, interview_progress, interview_status, answer],
        )
        end_button.click(
            fn=partial(end_interview_view, service),
            inputs=[session_id],
            outputs=[current_question, interview_progress, interview_status, answer],
        )
        generate_report_button.click(
            fn=partial(generate_report_view, service, reports_dir),
            inputs=[session_id],
            outputs=[
                overall_score, evidence_coverage, category_scores, strengths,
                areas_to_validate, evidence, human_follow_ups, transcript,
                recommendation, report_summary, report_download,
            ],
        )

    return app


def _prepare_request(
    company_name: str,
    job_title: str,
    experience_level: str | None,
    job_description_text: str,
    job_description_file: str | None,
    candidate_cv_file: str | None,
    num_questions: int | float,
    approximate_duration: int | float,
) -> PrepareInterviewRequest:
    if not candidate_cv_file:
        raise ValueError("Candidate CV upload is required.")
    text = job_description_text.strip()
    if text and job_description_file:
        raise ValueError("Provide either Job Description text or a file, not both.")
    if not text and not job_description_file:
        raise ValueError("Job Description text or file is required.")
    return PrepareInterviewRequest(
        company_name=company_name,
        job_title=job_title,
        experience_level=experience_level or None,
        job_description_text=text or None,
        job_description_path=Path(job_description_file) if job_description_file else None,
        candidate_cv_path=Path(candidate_cv_file),
        num_questions=int(num_questions),
        approximate_duration_minutes=int(approximate_duration),
    )


def _progress_callback(
    progress: gr.Progress,
    messages: list[str],
) -> Callable[[str], None]:
    def report(message: str) -> None:
        messages.append(message)
        progress((min(len(messages), 7), 7), desc=message)

    return report


def plan_to_rows(plan: InterviewPlan) -> PlanRows:
    return [
        [
            question.id,
            question.category.value,
            question.question,
            question.purpose,
            "; ".join(question.expected_topics),
            question.difficulty,
            question.follow_up_allowed,
        ]
        for question in plan.questions
    ]


def rows_to_questions(rows: PlanRows | None) -> list[InterviewQuestion]:
    normalized_rows = _normalize_rows(rows)
    return [
        InterviewQuestion(
            id=_cell(row, 0),
            category=_cell(row, 1),
            question=_cell(row, 2),
            purpose=_cell(row, 3),
            expected_topics=_topic_cells(row),
            difficulty=_cell(row, 5),
            follow_up_allowed=_bool_cell(row, 6),
        )
        for row in normalized_rows
    ]


def report_to_view(report: HRReport, report_path: Path) -> ReportView:
    category_rows = [
        [
            category.category.value.replace("_", " ").title(),
            str(category.score) if category.score is not None else "N/A",
            category.reasoning,
            "\n".join(category.evidence) or "Insufficient evidence",
            "\n".join(category.areas_to_validate) or "None recorded",
        ]
        for category in report.category_scores
    ]
    return (
        (
            f"{report.overall_score}/100"
            if report.overall_score is not None
            else "N/A — Insufficient evidence coverage"
        ),
        f"{report.evidence_coverage:.0%}",
        category_rows,
        _markdown_section(
            "Strengths",
            _markdown_list(report.strengths, "No strong categories identified."),
        ),
        _markdown_section(
            "Areas requiring validation",
            _markdown_list(report.areas_requiring_validation, "No additional areas recorded."),
        ),
        _markdown_section(
            "Strong evidence",
            _markdown_list(report.strong_evidence, "No strong transcript evidence identified."),
        ),
        _markdown_section(
            "Human follow-up questions",
            _markdown_list(report.human_follow_up_questions, "No follow-up questions generated."),
        ),
        _markdown_section("Full transcript", _transcript_markdown(report)),
        report.recommendation.value,
        _markdown_section("Interview summary", report.interview_summary),
        str(report_path),
    )


def _prompt_view(prompt: NextPrompt, company: str, role: str) -> PromptView:
    if prompt.finished:
        return "Interview complete. Thank you.", "100% complete", _status_line(prompt.state.value), ""
    number = min(prompt.question_index + 1, prompt.total_questions)
    kind = "Follow-up" if prompt.is_follow_up else f"Question {number} of {prompt.total_questions}"
    question = f"{kind}\n\n{prompt.question_text or ''}"
    progress = f"{prompt.progress_pct:.0f}% complete · {company} · {role}"
    return question, progress, _status_line(prompt.state.value), ""


def _normalize_rows(rows: PlanRows | None) -> PlanRows:
    if rows is None:
        return []
    return [list(row) for row in rows]


def _next_custom_question_id(rows: PlanRows) -> str:
    existing = {_cell(row, 0) for row in rows if row}
    index = 1
    while f"hr-custom-{index}" in existing:
        index += 1
    return f"hr-custom-{index}"


def _cell(row: PlanRow, index: int) -> str:
    if index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def _topic_cells(row: PlanRow) -> list[str]:
    return [topic.strip() for topic in _cell(row, 4).split(";") if topic.strip()]


def _bool_cell(row: PlanRow, index: int) -> bool:
    if index >= len(row):
        return False
    value = row[index]
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _markdown_list(items: list[str], empty_message: str) -> str:
    return "\n".join(f"- {item}" for item in items) if items else f"_{empty_message}_"


def _markdown_section(title: str, content: str) -> str:
    return f"### {title}\n\n{content}"


def _transcript_markdown(report: HRReport) -> str:
    if not report.full_transcript:
        return "_No transcript turns were recorded._"
    sections: list[str] = []
    for index, turn in enumerate(report.full_transcript, start=1):
        label = "Follow-up" if turn.is_follow_up else "Question"
        sections.append(f"**{label} {index}:** {turn.question}\n\n> {turn.answer}")
    return "\n\n---\n\n".join(sections)


def _status_line(state: str) -> str:
    return f"Status: **{state.replace('_', ' ').title()}**"
