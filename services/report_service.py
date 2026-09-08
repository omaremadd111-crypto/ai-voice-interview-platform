"""Assembles, renders, and saves the HR report -- plus the safety guard that
rejects autonomous hiring-decision language before a report is ever returned.

Verbatim candidate/transcript text (full_transcript, important_candidate_answers,
and evidence snippets, which are always trimmed substrings of an actual answer)
is exempt from the banned-word guard: the guard targets our OWN generated
commentary/reasoning/recommendation, not a candidate happening to say a word
like "hired" while describing their own work history.
"""
from datetime import datetime, timezone
from pathlib import Path
import re

from models.candidate import CandidateAnalysis
from models.common import EvaluationCategory, RecommendationLevel, ScreeningOutcome
from models.evaluation import CategoryEvaluation, HRReport, InterviewEvaluation
from models.interview import InterviewPlan, InterviewTranscript
from models.session import InterviewSession
from services.logging_service import Event, log_event

_BANNED_DECISION_WORDS = ("hire", "reject", "disqualif")
_STRENGTH_SCORE_THRESHOLD = 75
_MAX_RECRUITER_ITEMS = 5
_DEDUP_STOPWORDS = {
    "a", "an", "and", "candidate", "concrete", "detail", "details", "for", "in",
    "of", "on", "real", "specific", "the", "their", "to", "whether", "with",
}


class ReportSafetyError(Exception):
    """Raised when a generated HR report would violate hiring-safety constraints."""


def _category_label(category: EvaluationCategory) -> str:
    return category.value.replace("_", " ").title()


def _concern_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", value.casefold()):
        if token in _DEDUP_STOPWORDS:
            continue
        if token == "apis":
            token = "api"
        elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.add(token)
    return tokens


def _same_concern(first: str, second: str) -> bool:
    first_tokens = _concern_tokens(first)
    second_tokens = _concern_tokens(second)
    if not first_tokens or not second_tokens:
        return first.strip().casefold() == second.strip().casefold()
    overlap = len(first_tokens & second_tokens)
    smaller = min(len(first_tokens), len(second_tokens))
    union = len(first_tokens | second_tokens)
    return overlap / smaller >= 0.75 or overlap / union >= 0.6


def _dedup_concerns(items: list[str], limit: int | None = None) -> list[str]:
    result: list[str] = []
    for raw_item in items:
        item = " ".join(raw_item.split()).strip(" -")
        if not item or any(_same_concern(item, existing) for existing in result):
            continue
        result.append(item)
        if limit is not None and len(result) >= limit:
            break
    return result


def _build_candidate_overview(candidate_analysis: CandidateAnalysis | None) -> str:
    if candidate_analysis is None:
        return "No candidate analysis available."
    parts: list[str] = []
    if candidate_analysis.skills:
        parts.append(f"Skills include {', '.join(candidate_analysis.skills[:5])}.")
    if candidate_analysis.experience:
        parts.append(f"Experience noted: {candidate_analysis.experience[0]}")
    if candidate_analysis.education:
        parts.append(f"Education: {candidate_analysis.education[0]}")
    return " ".join(parts) if parts else "Limited information available from the CV."


def _build_interview_summary(transcript: InterviewTranscript, plan: InterviewPlan) -> str:
    total_planned_main = len(plan.questions)
    answered_question_ids = {turn.question_id for turn in transcript.turns if not turn.is_follow_up}
    answered_main = sum(1 for question in plan.questions if question.id in answered_question_ids)
    total_turns = len(transcript.turns)
    follow_up_turns = sum(1 for t in transcript.turns if t.is_follow_up)
    follow_up_clause = f" with {follow_up_turns} follow-up exchange(s)" if follow_up_turns else ""
    return (
        f"The candidate answered {answered_main} of {total_planned_main} planned main "
        f"question(s){follow_up_clause} across {total_turns} total turn(s)."
    )


def _build_strong_evidence(category_evaluations: list[CategoryEvaluation]) -> list[str]:
    items = [e for ce in category_evaluations if ce.sufficient_evidence for e in ce.evidence]
    return _dedup_concerns(items, _MAX_RECRUITER_ITEMS)


def _build_strengths(category_evaluations: list[CategoryEvaluation]) -> list[str]:
    strengths = [
        f"{_category_label(ce.category)}: strong evidence (score {ce.score}/100)."
        for ce in category_evaluations
        if ce.score is not None and ce.score >= _STRENGTH_SCORE_THRESHOLD
    ]
    return strengths[:_MAX_RECRUITER_ITEMS]


def _build_areas_requiring_validation(category_evaluations: list[CategoryEvaluation]) -> list[str]:
    actionable_areas: list[str] = []
    category_gaps: list[str] = []
    for ce in category_evaluations:
        if not ce.sufficient_evidence:
            category_gaps.append(
                f"{_category_label(ce.category)}: insufficient evidence from this interview."
            )
        actionable_areas.extend(ce.areas_to_validate)
    return _dedup_concerns(
        [*actionable_areas, *category_gaps],
        _MAX_RECRUITER_ITEMS,
    )


def _build_important_answers(
    category_evaluations: list[CategoryEvaluation], transcript: InterviewTranscript,
) -> list[str]:
    evidence_snippets = {e for ce in category_evaluations for e in ce.evidence}
    important: list[str] = []
    seen_answers: set[str] = set()
    for turn in transcript.turns:
        if not turn.answer or turn.answer in seen_answers:
            continue
        for snippet in evidence_snippets:
            stripped = (snippet[:-1] if snippet.endswith("…") else snippet).strip()
            if stripped and stripped in turn.answer:
                important.append(turn.answer)
                seen_answers.add(turn.answer)
                break
    return important[:_MAX_RECRUITER_ITEMS]


def _validation_area_to_question(area: str) -> str:
    cleaned = " ".join(area.strip().rstrip(".?").split())
    lower = cleaned.casefold()
    if "insufficient evidence from this interview" in lower and ":" in cleaned:
        category = cleaned.split(":", 1)[0].casefold()
        return (
            f"Can you share a concrete example that demonstrates your {category} "
            "for this role, including what you did and the outcome?"
        )
    if lower.startswith("whether "):
        cleaned = cleaned[8:]
        return f"Can you clarify {cleaned}?"
    if lower.startswith("the candidate's "):
        cleaned = f"your {cleaned[16:]}"
    return (
        f"Can you describe a concrete project or situation that demonstrates {cleaned}, "
        "including your responsibilities, actions, and the outcome?"
    )


def _build_human_follow_up_questions(category_evaluations: list[CategoryEvaluation]) -> list[str]:
    areas = _build_areas_requiring_validation(category_evaluations)
    return [_validation_area_to_question(area) for area in areas]


def build_hr_report(session: InterviewSession, evaluation: InterviewEvaluation) -> HRReport:
    if session.interview_plan is None:
        raise ValueError("Session has no interview plan; cannot build a report.")

    if session.job_analysis is not None:
        role = session.job_analysis.job_title
    elif session.job_input is not None:
        role = session.job_input.job_title
    else:
        role = "Unknown role"

    candidate_name = session.candidate_input.full_name if session.candidate_input else None
    if candidate_name is None and session.candidate_analysis is not None:
        candidate_name = session.candidate_analysis.full_name

    report_overall_score = evaluation.overall_score
    if evaluation.recommendation == RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW:
        report_overall_score = None

    report = HRReport(
        session_id=session.id,
        company=session.company,
        role=role,
        candidate_name=candidate_name,
        candidate_overview=_build_candidate_overview(session.candidate_analysis),
        interview_summary=_build_interview_summary(session.transcript, session.interview_plan),
        overall_score=report_overall_score,
        evidence_coverage=evaluation.evidence_coverage,
        category_scores=evaluation.category_evaluations,
        strong_evidence=_build_strong_evidence(evaluation.category_evaluations),
        strengths=_build_strengths(evaluation.category_evaluations),
        areas_requiring_validation=_build_areas_requiring_validation(evaluation.category_evaluations),
        important_candidate_answers=_build_important_answers(evaluation.category_evaluations, session.transcript),
        human_follow_up_questions=_build_human_follow_up_questions(evaluation.category_evaluations),
        full_transcript=list(session.transcript.turns),
        recommendation=evaluation.recommendation,
        screening_outcome=evaluation.screening_outcome,
        llm_provider=session.llm_provider,
        is_mock=session.is_mock,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    _assert_report_is_safe(report)

    log_event(
        Event.REPORT_CREATED,
        session_id=session.id,
        overall_score=report.overall_score,
        recommendation=report.recommendation.value,
        screening_outcome=report.screening_outcome.value,
        evidence_coverage=report.evidence_coverage,
    )
    return report


def _assert_report_is_safe(report: HRReport) -> None:
    """Guard against autonomous hiring-decision language. Only the raw transcript
    and other verbatim-candidate-text fields are exempt; every field that reflects
    our own generated commentary, reasoning, or recommendation is checked, and a
    violation raises rather than being silently dropped or corrected."""
    if not isinstance(report.recommendation, RecommendationLevel):
        raise ReportSafetyError("Report recommendation is not a valid RecommendationLevel.")
    if not isinstance(report.screening_outcome, ScreeningOutcome):
        raise ReportSafetyError("Report screening_outcome is not a valid ScreeningOutcome.")

    checked_text_parts = [
        report.candidate_overview,
        report.interview_summary,
        report.recommendation.value,
        report.screening_outcome.value,
        *report.strengths,
        *report.areas_requiring_validation,
        *report.human_follow_up_questions,
        *[ce.reasoning for ce in report.category_scores],
        *[area for ce in report.category_scores for area in ce.areas_to_validate],
    ]
    combined = " ".join(checked_text_parts).lower()
    for banned in _BANNED_DECISION_WORDS:
        if banned in combined:
            raise ReportSafetyError(
                f"Generated report contains a banned hiring-decision word ('{banned}') "
                "outside the transcript or other verbatim candidate text."
            )


def render_report_markdown(report: HRReport) -> str:
    lines: list[str] = [f"# HR Interview Report — {report.company}", ""]
    if report.is_mock:
        lines += [
            "> **Demo / Mock Mode** — this report was generated by a deterministic "
            "local heuristic, not a real LLM.",
            "",
        ]

    lines += [
        "## Candidate Overview",
        report.candidate_name or "(name not provided)",
        report.candidate_overview,
        "",
        "## Role",
        f"{report.role} at {report.company}",
        "",
        "## Interview Summary",
        report.interview_summary,
        "",
        "## Overall Score",
        (
            f"{report.overall_score}/100 (evidence coverage: {report.evidence_coverage:.0%})"
            if report.overall_score is not None
            else (
                "N/A — Insufficient evidence coverage "
                f"({report.evidence_coverage:.0%})"
            )
        ),
        "",
        "## Initial Screening Result",
        report.screening_outcome.value,
        (
            "*This is a configurable initial screening result, not an employment "
            "decision. PASS does not mean the candidate should be hired; FAIL does "
            "not mean the candidate was rejected. The human recruiter remains the "
            "final decision maker.*"
        ),
        "",
        "## Category Scores",
    ]
    for ce in report.category_scores:
        score_text = f"{ce.score}/100" if ce.score is not None else "Insufficient evidence"
        lines += [
            f"### {_category_label(ce.category)} — {score_text}",
            f"- **Reasoning:** {ce.reasoning}",
            "- **Evidence:**",
        ]
        lines += [f"  - {item}" for item in ce.evidence] or ["  - (none)"]
        if ce.areas_to_validate:
            lines += ["- **Areas to validate:**"]
            lines += [f"  - {item}" for item in _dedup_concerns(ce.areas_to_validate, 3)]

    lines += ["", "## Strong Evidence"]
    lines += [f"- {e}" for e in report.strong_evidence] or ["- (none)"]

    lines += ["", "## Strengths"]
    lines += [f"- {s}" for s in report.strengths] or ["- (none identified)"]

    lines += ["", "## Areas Requiring Further Validation"]
    lines += [f"- {a}" for a in report.areas_requiring_validation] or ["- (none)"]

    lines += ["", "## Important Candidate Answers"]
    lines += [f"- {a}" for a in report.important_candidate_answers] or ["- (none)"]

    lines += ["", "## Human Interview Follow-Up Questions"]
    lines += [f"- {q}" for q in report.human_follow_up_questions] or ["- (none)"]

    lines += ["", "## Full Transcript"]
    for turn in report.full_transcript:
        tag = "Follow-up" if turn.is_follow_up else "Question"
        lines.append(f"**{tag} ({turn.category.value})**: {turn.question}")
        lines.append(f"> {turn.answer}")
        lines.append("")

    lines += [
        "## Final AI Recommendation",
        report.recommendation.value,
        "",
        "*The final hiring decision remains with the human recruiter or hiring "
        "manager. This report supports, but does not replace, human judgment.*",
    ]
    return "\n".join(lines)


def save_report(report: HRReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{report.session_id}.md"
    path.write_text(render_report_markdown(report), encoding="utf-8")
    return path
