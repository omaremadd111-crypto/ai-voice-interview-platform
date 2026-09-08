"""HR report tests: assembly, markdown rendering, saving, and the hiring-safety
guard (banned decision words outside verbatim candidate/transcript text).
"""
from pathlib import Path

import pytest

from models.candidate import CandidateAnalysis, CandidateInput
from models.common import (
    EvaluationCategory,
    InterviewState,
    QuestionCategory,
    RecommendationLevel,
    ScreeningOutcome,
)
from models.evaluation import CategoryEvaluation, InterviewEvaluation
from models.interview import InterviewPlan, InterviewQuestion, InterviewTranscript, InterviewTurn
from models.job import JobAnalysis
from models.session import InterviewSession
from services.report_service import ReportSafetyError, build_hr_report, render_report_markdown, save_report

_NOW = "2026-08-19T00:00:00+00:00"
EC = EvaluationCategory


def _ce(category: EvaluationCategory, score: int | None, reasoning: str = "test reasoning",
        evidence: list[str] | None = None, areas_to_validate: list[str] | None = None) -> CategoryEvaluation:
    sufficient = score is not None
    return CategoryEvaluation(
        category=category, score=score, sufficient_evidence=sufficient,
        reasoning=reasoning if sufficient else "Insufficient evidence",
        evidence=evidence if evidence is not None else (["some evidence"] if sufficient else []),
        areas_to_validate=areas_to_validate or [],
    )


def _plan() -> InterviewPlan:
    return InterviewPlan(questions=[
        InterviewQuestion(id="q1", category=QuestionCategory.TECHNICAL, question="Q1?",
                           purpose="p", expected_topics=["Python"], difficulty="medium", follow_up_allowed=True),
    ])


def _session(transcript: InterviewTranscript | None = None, candidate_full_name: str | None = "Jordan Rivera") -> InterviewSession:
    return InterviewSession(
        id="sess-1", company="FlairsTech",
        job_analysis=JobAnalysis(job_title="Junior AI Engineer", required_skills=["Python"]),
        candidate_input=CandidateInput(full_name=candidate_full_name, cv_text="cv text"),
        candidate_analysis=CandidateAnalysis(skills=["Python"], experience=["2 years experience."],
                                              education=["BSc Computer Science."]),
        interview_plan=_plan(),
        transcript=transcript or InterviewTranscript(turns=[
            InterviewTurn(question_id="q1", question="Q1?", category=QuestionCategory.TECHNICAL,
                          answer="I used Python extensively.", is_follow_up=False, timestamp=_NOW),
        ]),
        state=InterviewState.COMPLETED, created_at=_NOW, updated_at=_NOW,
        llm_provider="mock", is_mock=True,
    )


def _evaluation(
    category_evaluations: list[CategoryEvaluation], overall_score: int | None = 70,
    coverage: float = 0.9,
    recommendation: RecommendationLevel = RecommendationLevel.PROCEED_TO_DEEPER_TECHNICAL_ASSESSMENT,
    screening_outcome: ScreeningOutcome = ScreeningOutcome.NEEDS_REVIEW,
) -> InterviewEvaluation:
    return InterviewEvaluation(
        category_evaluations=category_evaluations, overall_score=overall_score,
        evidence_coverage=coverage, recommendation=recommendation,
        screening_outcome=screening_outcome, rubric_profile="technical",
    )


# ---- assembly ----

def test_build_hr_report_assembles_expected_fields() -> None:
    evaluations = [_ce(EC.TECHNICAL_KNOWLEDGE, 80)]
    report = build_hr_report(_session(), _evaluation(evaluations))
    assert report.session_id == "sess-1"
    assert report.company == "FlairsTech"
    assert report.role == "Junior AI Engineer"
    assert report.candidate_name == "Jordan Rivera"
    assert report.overall_score == 70
    assert report.recommendation == RecommendationLevel.PROCEED_TO_DEEPER_TECHNICAL_ASSESSMENT
    assert len(report.full_transcript) == 1


def test_build_hr_report_falls_back_to_job_input_when_no_analysis() -> None:
    session = _session()
    session.job_analysis = None
    from models.job import JobInput
    session.job_input = JobInput(company_name="FlairsTech", job_title="Backend Engineer", description_text="x")
    report = build_hr_report(session, _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)]))
    assert report.role == "Backend Engineer"


def test_candidate_name_falls_back_to_explicit_cv_analysis_name() -> None:
    session = _session(candidate_full_name=None)
    session.candidate_analysis.full_name = "Jordan Rivera"
    report = build_hr_report(
        session,
        _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)]),
    )
    assert report.candidate_name == "Jordan Rivera"
    assert "Jordan Rivera" in render_report_markdown(report)


def test_strengths_derived_from_high_scoring_categories() -> None:
    evaluations = [_ce(EC.TECHNICAL_KNOWLEDGE, 90), _ce(EC.COMMUNICATION, 40)]
    report = build_hr_report(_session(), _evaluation(evaluations))
    assert any("Technical Knowledge" in s for s in report.strengths)
    assert not any("Communication" in s for s in report.strengths)


def test_areas_requiring_validation_includes_insufficient_evidence_categories() -> None:
    evaluations = [_ce(EC.TECHNICAL_KNOWLEDGE, 80), _ce(EC.COMMUNICATION, None)]
    report = build_hr_report(_session(), _evaluation(evaluations))
    assert any("Communication" in a for a in report.areas_requiring_validation)


def test_interview_summary_counts_only_main_questions_actually_answered() -> None:
    plan = InterviewPlan(questions=[
        InterviewQuestion(
            id=f"q{i}", category=QuestionCategory.TECHNICAL, question=f"Q{i}?",
            purpose="p", expected_topics=["Python"], difficulty="medium",
        )
        for i in range(1, 4)
    ])
    transcript = InterviewTranscript(turns=[
        InterviewTurn(
            question_id="q1", question="Q1?", category=QuestionCategory.TECHNICAL,
            answer="A substantive first answer.", is_follow_up=False, timestamp=_NOW,
        ),
        InterviewTurn(
            question_id="q1", question="Can you elaborate?", category=QuestionCategory.TECHNICAL,
            answer="Additional detail.", is_follow_up=True, timestamp=_NOW,
        ),
    ])
    session = _session(transcript=transcript)
    session.interview_plan = plan

    report = build_hr_report(session, _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)]))

    assert "answered 1 of 3 planned main question(s)" in report.interview_summary
    assert "1 follow-up exchange(s)" in report.interview_summary
    assert "2 total turn(s)" in report.interview_summary


# ---- markdown rendering ----

def test_markdown_contains_all_spec_sections() -> None:
    report = build_hr_report(_session(), _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)]))
    md = render_report_markdown(report)
    for header in (
        "Candidate Overview", "Role", "Interview Summary", "Overall Score", "Category Scores",
        "Strong Evidence", "Strengths", "Areas Requiring Further Validation",
        "Important Candidate Answers", "Human Interview Follow-Up Questions",
        "Full Transcript", "Final AI Recommendation",
    ):
        assert f"## {header}" in md


def test_markdown_labels_mock_mode() -> None:
    report = build_hr_report(_session(), _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)]))
    md = render_report_markdown(report)
    assert "Demo / Mock Mode" in md


def test_markdown_states_human_remains_decision_maker() -> None:
    report = build_hr_report(_session(), _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)]))
    md = render_report_markdown(report)
    assert "human recruiter" in md.lower()
    assert "final hiring decision remains" in md.lower()


def test_markdown_handles_none_overall_score() -> None:
    evaluations = [_ce(EC.TECHNICAL_KNOWLEDGE, None)]
    report = build_hr_report(_session(), _evaluation(evaluations, overall_score=None,
                                                       recommendation=RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW))
    md = render_report_markdown(report)
    assert "N/A" in md


def test_low_coverage_report_suppresses_misleading_final_score() -> None:
    evaluations = [_ce(EC.TECHNICAL_KNOWLEDGE, 90)]
    report = build_hr_report(
        _session(),
        _evaluation(
            evaluations,
            overall_score=90,
            coverage=0.35,
            recommendation=RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW,
        ),
    )
    md = render_report_markdown(report)
    assert report.overall_score is None
    assert "## Overall Score\nN/A — Insufficient evidence coverage (35%)" in md
    assert "## Overall Score\n90/100" not in md


def test_report_deduplicates_and_caps_recruiter_facing_sections() -> None:
    areas = [
        "specific REST API experience",
        "REST APIs experience in a real project",
        "stakeholder interview techniques",
        "techniques for stakeholder interviews",
        "SQL query analysis",
        "process mapping with BPMN",
        "writing acceptance criteria",
        "UAT planning",
        "Agile backlog refinement",
    ]
    evaluations = [
        _ce(category, 90, areas_to_validate=areas)
        for category in EC
    ]
    report = build_hr_report(_session(), _evaluation(evaluations, overall_score=90))

    assert len(report.strengths) == 5
    assert len(report.strong_evidence) <= 5
    assert len(report.areas_requiring_validation) <= 5
    assert len(report.human_follow_up_questions) <= 5
    assert sum("rest api" in area.casefold() for area in report.areas_requiring_validation) == 1
    assert all(question.endswith("?") for question in report.human_follow_up_questions)
    assert all(not question.startswith("Follow up on:") for question in report.human_follow_up_questions)
    assert all("responsibilities" in question for question in report.human_follow_up_questions)


def test_unscored_category_gap_becomes_a_real_follow_up_question() -> None:
    report = build_hr_report(
        _session(),
        _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, None)]),
    )
    assert len(report.human_follow_up_questions) == 1
    question = report.human_follow_up_questions[0]
    assert question.endswith("?")
    assert "technical knowledge" in question.casefold()
    assert not question.startswith("Follow up on:")


def test_markdown_category_scores_remain_scannable_with_reasoning_and_evidence() -> None:
    report = build_hr_report(
        _session(),
        _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80, evidence=["some evidence"])]),
    )
    md = render_report_markdown(report)
    assert "### Technical Knowledge — 80/100" in md
    assert "**Reasoning:**" in md
    assert "**Evidence:**" in md


# ---- save ----

def test_save_report_writes_valid_utf8_file(tmp_path: Path) -> None:
    report = build_hr_report(_session(), _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)]))
    path = save_report(report, tmp_path)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "sess-1" in path.name
    assert "# HR Interview Report" in text


# ---- screening outcome ----

def test_report_carries_screening_outcome_through_from_evaluation() -> None:
    report = build_hr_report(
        _session(),
        _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)], screening_outcome=ScreeningOutcome.PASS),
    )
    assert report.screening_outcome == ScreeningOutcome.PASS


def test_markdown_shows_screening_outcome_with_non_hiring_disclaimer() -> None:
    report = build_hr_report(
        _session(),
        _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)], screening_outcome=ScreeningOutcome.FAIL),
    )
    md = render_report_markdown(report)
    assert "## Initial Screening Result" in md
    assert "FAIL" in md
    assert "not an employment decision" in md
    assert "human recruiter remains the final decision maker" in md


@pytest.mark.parametrize("outcome", [ScreeningOutcome.PASS, ScreeningOutcome.FAIL, ScreeningOutcome.NEEDS_REVIEW])
def test_screening_outcome_never_trips_the_banned_word_guard(outcome: ScreeningOutcome) -> None:
    # The enum values themselves ("PASS"/"FAIL"/"NEEDS_REVIEW") contain none of the
    # banned substrings, so a clean report must build regardless of which outcome.
    report = build_hr_report(_session(), _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)], screening_outcome=outcome))
    assert report.screening_outcome == outcome


# ---- safety guard ----

def test_recommendation_is_always_a_valid_enum_member() -> None:
    report = build_hr_report(_session(), _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)]))
    assert isinstance(report.recommendation, RecommendationLevel)


def test_screening_outcome_is_always_a_valid_enum_member() -> None:
    report = build_hr_report(_session(), _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)]))
    assert isinstance(report.screening_outcome, ScreeningOutcome)


@pytest.mark.parametrize("banned_word", ["hire", "reject", "disqualify", "disqualified"])
def test_guard_rejects_banned_word_in_category_reasoning(banned_word: str) -> None:
    evaluations = [_ce(EC.TECHNICAL_KNOWLEDGE, 80, reasoning=f"This candidate should {banned_word} well.")]
    with pytest.raises(ReportSafetyError):
        build_hr_report(_session(), _evaluation(evaluations))


def test_guard_rejects_banned_word_in_areas_to_validate() -> None:
    evaluations = [_ce(EC.TECHNICAL_KNOWLEDGE, 80, areas_to_validate=["whether to reject this application"])]
    with pytest.raises(ReportSafetyError):
        build_hr_report(_session(), _evaluation(evaluations))


def test_guard_does_not_false_positive_on_verbatim_candidate_text_containing_hired() -> None:
    # A candidate saying "I was hired..." while describing their own history is normal
    # language and must never trip the guard -- only OUR generated commentary is checked.
    transcript = InterviewTranscript(turns=[
        InterviewTurn(question_id="q1", question="Q1?", category=QuestionCategory.TECHNICAL,
                      answer="I was hired as a junior developer and later promoted.",
                      is_follow_up=False, timestamp=_NOW),
    ])
    evaluations = [_ce(
        EC.TECHNICAL_KNOWLEDGE, 80,
        evidence=["I was hired as a junior developer and later promoted."],
    )]
    report = build_hr_report(_session(transcript=transcript), _evaluation(evaluations))
    assert any("hired" in t.answer.lower() for t in report.full_transcript)
    assert any("hired" in a.lower() for a in report.important_candidate_answers)


def test_guard_checked_fields_cover_reasoning_and_recommendation_but_not_transcript() -> None:
    # Sanity check on the guard's own field split: the transcript/evidence-derived
    # fields are exempt, but reasoning is not -- proven by the two tests above.
    from services.report_service import _assert_report_is_safe
    report = build_hr_report(_session(), _evaluation([_ce(EC.TECHNICAL_KNOWLEDGE, 80)]))
    _assert_report_is_safe(report)  # must not raise for a clean report
