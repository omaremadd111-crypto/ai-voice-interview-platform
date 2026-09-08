"""Pure-Python scoring tests: weighted overall score, renormalization, evidence
coverage, and deterministic recommendation thresholds. No LLM involved anywhere.
"""
import ast
from pathlib import Path

import pytest

from config.rubric_config import RubricProfile, RubricThresholds, load_rubric_config
from models.common import EvaluationCategory, RecommendationLevel, ScreeningOutcome
from models.evaluation import CategoryEvaluation
from services.scoring import (
    build_interview_evaluation,
    compute_evidence_coverage,
    compute_overall_score,
    determine_recommendation,
    determine_screening_outcome,
)

EC = EvaluationCategory


def _ce(category: EvaluationCategory, score: int | None, evidence: list[str] | None = None) -> CategoryEvaluation:
    sufficient = score is not None
    return CategoryEvaluation(
        category=category, score=score, sufficient_evidence=sufficient,
        reasoning="test reasoning" if sufficient else "Insufficient evidence",
        evidence=evidence if evidence is not None else (["some evidence"] if sufficient else []),
        areas_to_validate=[],
    )


@pytest.fixture
def profile() -> RubricProfile:
    return RubricProfile(description="test profile", weights={
        EC.TECHNICAL_KNOWLEDGE: 30, EC.RELEVANT_EXPERIENCE: 25, EC.PROBLEM_SOLVING: 20,
        EC.JOB_REQUIREMENT_COVERAGE: 15, EC.COMMUNICATION: 10, EC.BEHAVIORAL_COMPETENCIES: 0,
    })


@pytest.fixture
def thresholds() -> RubricThresholds:
    return RubricThresholds(min_evidence_coverage=0.5, strong_evidence_min_score=75, proceed_min_score=60)


# ---- structural guarantee ----

def test_scoring_module_imports_no_llm_code() -> None:
    tree = ast.parse(Path("services/scoring.py").read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    assert not any("llm" in m for m in modules), f"scoring.py imports LLM code: {modules}"


# ---- compute_overall_score ----

def test_overall_score_is_weighted_average_when_fully_scored(profile: RubricProfile) -> None:
    evaluations = [
        _ce(EC.TECHNICAL_KNOWLEDGE, 80), _ce(EC.RELEVANT_EXPERIENCE, 60),
        _ce(EC.PROBLEM_SOLVING, 70), _ce(EC.JOB_REQUIREMENT_COVERAGE, 50),
        _ce(EC.COMMUNICATION, 90), _ce(EC.BEHAVIORAL_COMPETENCIES, 40),
    ]
    expected = round((80*30 + 60*25 + 70*20 + 50*15 + 90*10 + 40*0) / 100)
    assert compute_overall_score(evaluations, profile) == expected


def test_overall_score_excludes_unscored_and_renormalizes(profile: RubricProfile) -> None:
    # Only technical_knowledge (30) and relevant_experience (25) scored; the other
    # weight (45) must NOT dilute the average -- it should renormalize over 55.
    evaluations = [
        _ce(EC.TECHNICAL_KNOWLEDGE, 90), _ce(EC.RELEVANT_EXPERIENCE, 60),
        _ce(EC.PROBLEM_SOLVING, None), _ce(EC.JOB_REQUIREMENT_COVERAGE, None),
        _ce(EC.COMMUNICATION, None), _ce(EC.BEHAVIORAL_COMPETENCIES, None),
    ]
    expected = round((90*30 + 60*25) / (30+25))
    assert compute_overall_score(evaluations, profile) == expected


def test_overall_score_none_when_nothing_scored(profile: RubricProfile) -> None:
    evaluations = [_ce(c, None) for c in EC]
    assert compute_overall_score(evaluations, profile) is None


def test_overall_score_ignores_zero_weight_category_regardless_of_score(profile: RubricProfile) -> None:
    with_behavioral = [
        _ce(EC.TECHNICAL_KNOWLEDGE, 80), _ce(EC.RELEVANT_EXPERIENCE, 60),
        _ce(EC.PROBLEM_SOLVING, 70), _ce(EC.JOB_REQUIREMENT_COVERAGE, 50),
        _ce(EC.COMMUNICATION, 90), _ce(EC.BEHAVIORAL_COMPETENCIES, 100),  # weight=0
    ]
    without_behavioral = with_behavioral[:-1] + [_ce(EC.BEHAVIORAL_COMPETENCIES, None)]
    assert compute_overall_score(with_behavioral, profile) == compute_overall_score(without_behavioral, profile)


def test_overall_score_bounded_0_to_100(profile: RubricProfile) -> None:
    evaluations = [_ce(c, 100) for c in EC]
    assert compute_overall_score(evaluations, profile) == 100
    evaluations_low = [_ce(c, 0) for c in EC]
    assert compute_overall_score(evaluations_low, profile) == 0


# ---- compute_evidence_coverage ----

def test_evidence_coverage_full_when_all_weighted_categories_scored(profile: RubricProfile) -> None:
    # behavioral_competencies has weight 0, so leaving it unscored still yields full coverage.
    evaluations = [
        _ce(EC.TECHNICAL_KNOWLEDGE, 80), _ce(EC.RELEVANT_EXPERIENCE, 60),
        _ce(EC.PROBLEM_SOLVING, 70), _ce(EC.JOB_REQUIREMENT_COVERAGE, 50),
        _ce(EC.COMMUNICATION, 90), _ce(EC.BEHAVIORAL_COMPETENCIES, None),
    ]
    assert compute_evidence_coverage(evaluations, profile) == pytest.approx(1.0)


def test_evidence_coverage_partial(profile: RubricProfile) -> None:
    evaluations = [
        _ce(EC.TECHNICAL_KNOWLEDGE, 80), _ce(EC.RELEVANT_EXPERIENCE, None),
        _ce(EC.PROBLEM_SOLVING, None), _ce(EC.JOB_REQUIREMENT_COVERAGE, None),
        _ce(EC.COMMUNICATION, None), _ce(EC.BEHAVIORAL_COMPETENCIES, None),
    ]
    assert compute_evidence_coverage(evaluations, profile) == pytest.approx(0.30)


def test_evidence_coverage_zero_when_nothing_scored(profile: RubricProfile) -> None:
    evaluations = [_ce(c, None) for c in EC]
    assert compute_evidence_coverage(evaluations, profile) == 0.0


def test_evidence_coverage_bounded_0_to_1(profile: RubricProfile) -> None:
    evaluations = [_ce(c, 50) for c in EC]
    coverage = compute_evidence_coverage(evaluations, profile)
    assert 0.0 <= coverage <= 1.0


# ---- determine_recommendation ----

def test_recommendation_insufficient_evidence_below_coverage_threshold(thresholds: RubricThresholds) -> None:
    assert determine_recommendation(0.3, 90, thresholds) == RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW


def test_recommendation_insufficient_evidence_when_score_is_none(thresholds: RubricThresholds) -> None:
    assert determine_recommendation(0.9, None, thresholds) == RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW


def test_recommendation_strong_evidence_at_and_above_threshold(thresholds: RubricThresholds) -> None:
    assert determine_recommendation(1.0, 75, thresholds) == RecommendationLevel.STRONG_EVIDENCE_FOR_HUMAN_REVIEW
    assert determine_recommendation(1.0, 95, thresholds) == RecommendationLevel.STRONG_EVIDENCE_FOR_HUMAN_REVIEW


def test_recommendation_proceed_in_60_to_74_range(thresholds: RubricThresholds) -> None:
    assert determine_recommendation(1.0, 60, thresholds) == RecommendationLevel.PROCEED_TO_DEEPER_TECHNICAL_ASSESSMENT
    assert determine_recommendation(1.0, 74, thresholds) == RecommendationLevel.PROCEED_TO_DEEPER_TECHNICAL_ASSESSMENT


def test_recommendation_requires_validation_below_60(thresholds: RubricThresholds) -> None:
    assert determine_recommendation(1.0, 59, thresholds) == RecommendationLevel.REQUIRES_ADDITIONAL_VALIDATION
    assert determine_recommendation(1.0, 0, thresholds) == RecommendationLevel.REQUIRES_ADDITIONAL_VALIDATION


def test_recommendation_only_uses_allowed_enum_values(thresholds: RubricThresholds) -> None:
    allowed = {
        RecommendationLevel.STRONG_EVIDENCE_FOR_HUMAN_REVIEW,
        RecommendationLevel.PROCEED_TO_DEEPER_TECHNICAL_ASSESSMENT,
        RecommendationLevel.REQUIRES_ADDITIONAL_VALIDATION,
        RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW,
    }
    for coverage in (0.0, 0.3, 0.5, 0.8, 1.0):
        for score in (None, 0, 40, 59, 60, 74, 75, 100):
            assert determine_recommendation(coverage, score, thresholds) in allowed


# ---- determine_screening_outcome ----
#
# PASS/FAIL are initial SCREENING triage, never a hiring decision. NEEDS_REVIEW
# covers every case where the evidence itself is insufficient; FAIL is reached
# only when there WAS sufficient evidence and the score fell short.

def test_screening_needs_review_below_coverage_threshold(thresholds: RubricThresholds) -> None:
    # Low evidence coverage: regardless of the raw score, we do not know enough to
    # say pass or fail -- this is the "low evidence behavior" case.
    assert determine_screening_outcome(0.3, 95, thresholds) == ScreeningOutcome.NEEDS_REVIEW


def test_screening_needs_review_when_score_is_none(thresholds: RubricThresholds) -> None:
    assert determine_screening_outcome(0.9, None, thresholds) == ScreeningOutcome.NEEDS_REVIEW


def test_screening_pass_at_and_above_threshold(thresholds: RubricThresholds) -> None:
    assert determine_screening_outcome(1.0, 60, thresholds) == ScreeningOutcome.PASS
    assert determine_screening_outcome(1.0, 100, thresholds) == ScreeningOutcome.PASS


def test_screening_fail_below_threshold_with_sufficient_evidence(thresholds: RubricThresholds) -> None:
    assert determine_screening_outcome(1.0, 59, thresholds) == ScreeningOutcome.FAIL
    assert determine_screening_outcome(1.0, 0, thresholds) == ScreeningOutcome.FAIL


def test_screening_outcome_uses_independently_configurable_threshold() -> None:
    # pass_score_threshold is a separate axis from the recommendation ladder: raising
    # it changes PASS/FAIL without touching strong_evidence_min_score/proceed_min_score.
    lenient = RubricThresholds(
        min_evidence_coverage=0.5, strong_evidence_min_score=75,
        proceed_min_score=60, pass_score_threshold=40,
    )
    strict = RubricThresholds(
        min_evidence_coverage=0.5, strong_evidence_min_score=75,
        proceed_min_score=60, pass_score_threshold=90,
    )
    assert determine_screening_outcome(1.0, 50, lenient) == ScreeningOutcome.PASS
    assert determine_screening_outcome(1.0, 50, strict) == ScreeningOutcome.FAIL
    # Recommendation is unaffected by pass_score_threshold.
    assert determine_recommendation(1.0, 50, lenient) == determine_recommendation(1.0, 50, strict)


def test_screening_outcome_only_uses_allowed_enum_values(thresholds: RubricThresholds) -> None:
    allowed = {ScreeningOutcome.PASS, ScreeningOutcome.FAIL, ScreeningOutcome.NEEDS_REVIEW}
    for coverage in (0.0, 0.3, 0.5, 0.8, 1.0):
        for score in (None, 0, 40, 59, 60, 74, 75, 100):
            assert determine_screening_outcome(coverage, score, thresholds) in allowed


def test_screening_outcome_never_equals_recommendation_vocabulary(thresholds: RubricThresholds) -> None:
    # Structural check that PASS/FAIL/NEEDS_REVIEW can never be mistaken for -- or
    # substitute as -- one of the four allowed recommendation phrasings.
    recommendation_values = {r.value for r in RecommendationLevel}
    for coverage in (0.0, 0.5, 1.0):
        for score in (None, 0, 60, 100):
            outcome = determine_screening_outcome(coverage, score, thresholds)
            assert outcome.value not in recommendation_values


# ---- build_interview_evaluation ----

def test_build_interview_evaluation_assembles_all_fields(profile: RubricProfile, thresholds: RubricThresholds) -> None:
    evaluations = [
        _ce(EC.TECHNICAL_KNOWLEDGE, 80), _ce(EC.RELEVANT_EXPERIENCE, 80),
        _ce(EC.PROBLEM_SOLVING, 80), _ce(EC.JOB_REQUIREMENT_COVERAGE, 80),
        _ce(EC.COMMUNICATION, 80), _ce(EC.BEHAVIORAL_COMPETENCIES, None),
    ]
    result = build_interview_evaluation(evaluations, profile, "technical", thresholds)
    assert result.overall_score == 80
    assert result.evidence_coverage == pytest.approx(1.0)
    assert result.recommendation == RecommendationLevel.STRONG_EVIDENCE_FOR_HUMAN_REVIEW
    assert result.screening_outcome == ScreeningOutcome.PASS
    assert result.rubric_profile == "technical"
    assert result.category_evaluations == evaluations


def test_build_interview_evaluation_with_real_rubrics_json(thresholds: RubricThresholds) -> None:
    rubric_config = load_rubric_config(Path("config/rubrics.json"))
    profile = rubric_config.get_profile(None)
    evaluations = [_ce(c, None) for c in EC]
    result = build_interview_evaluation(evaluations, profile, rubric_config.default_profile, rubric_config.thresholds)
    assert result.overall_score is None
    assert result.recommendation == RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW
    assert result.screening_outcome == ScreeningOutcome.NEEDS_REVIEW


def test_low_coverage_does_not_publish_renormalized_partial_score(
    profile: RubricProfile,
    thresholds: RubricThresholds,
) -> None:
    evaluations = [
        _ce(EC.TECHNICAL_KNOWLEDGE, 90),
        _ce(EC.JOB_REQUIREMENT_COVERAGE, 90),
        _ce(EC.RELEVANT_EXPERIENCE, None),
        _ce(EC.PROBLEM_SOLVING, None),
        _ce(EC.COMMUNICATION, None),
        _ce(EC.BEHAVIORAL_COMPETENCIES, None),
    ]
    assert compute_overall_score(evaluations, profile) == 90

    result = build_interview_evaluation(
        evaluations,
        profile,
        "technical",
        thresholds,
    )

    assert result.evidence_coverage == pytest.approx(0.45)
    assert result.overall_score is None
    assert result.recommendation == RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW
    assert result.screening_outcome == ScreeningOutcome.NEEDS_REVIEW  # low-evidence case: never FAIL


def test_build_interview_evaluation_screening_fail_with_full_evidence(
    profile: RubricProfile, thresholds: RubricThresholds,
) -> None:
    evaluations = [_ce(c, 20) for c in EC]
    result = build_interview_evaluation(evaluations, profile, "technical", thresholds)
    assert result.evidence_coverage == pytest.approx(1.0)
    assert result.overall_score == 20
    assert result.screening_outcome == ScreeningOutcome.FAIL
