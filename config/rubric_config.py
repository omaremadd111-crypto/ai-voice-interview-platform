"""Loader and validator for config/rubrics.json."""
import json
import math
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator, model_validator

from models.common import EvaluationCategory


class RubricConfigError(Exception):
    """Raised when config/rubrics.json is missing, malformed, or invalid."""


class RubricProfile(BaseModel):
    description: str
    weights: dict[EvaluationCategory, float]

    @field_validator("weights")
    @classmethod
    def _no_negative_weights(cls, v: dict[EvaluationCategory, float]) -> dict[EvaluationCategory, float]:
        for category, weight in v.items():
            if weight < 0:
                raise ValueError(f"Negative weight for {category}: {weight}")
        return v

    @model_validator(mode="after")
    def _all_categories_present(self) -> "RubricProfile":
        missing = set(EvaluationCategory) - set(self.weights.keys())
        if missing:
            names = sorted(m.value for m in missing)
            raise ValueError(f"Rubric profile is missing weights for: {names}")
        return self

    @model_validator(mode="after")
    def _weights_sum_to_100(self) -> "RubricProfile":
        total = sum(self.weights.values())
        if not math.isclose(total, 100.0, abs_tol=0.01):
            raise ValueError(f"Rubric weights must sum to 100, got {total}")
        return self


class RubricThresholds(BaseModel):
    min_evidence_coverage: float
    strong_evidence_min_score: int
    proceed_min_score: int
    # Independent axis from the recommendation ladder above: the score a candidate
    # must reach for the deterministic initial SCREENING result (PASS/FAIL/
    # NEEDS_REVIEW) to be PASS. Defaulted so existing configs/fixtures that predate
    # screening outcomes keep working unchanged.
    pass_score_threshold: int = 60

    @model_validator(mode="after")
    def _valid_ranges(self) -> "RubricThresholds":
        if not (0.0 <= self.min_evidence_coverage <= 1.0):
            raise ValueError("min_evidence_coverage must be within [0, 1]")
        if not (0 <= self.proceed_min_score <= self.strong_evidence_min_score <= 100):
            raise ValueError("thresholds must satisfy 0 <= proceed_min_score <= strong_evidence_min_score <= 100")
        if not (0 <= self.pass_score_threshold <= 100):
            raise ValueError("pass_score_threshold must be within [0, 100]")
        return self


class RubricConfig(BaseModel):
    profiles: dict[str, RubricProfile]
    default_profile: str
    thresholds: RubricThresholds

    @model_validator(mode="after")
    def _default_profile_exists(self) -> "RubricConfig":
        if self.default_profile not in self.profiles:
            raise ValueError(f"default_profile '{self.default_profile}' not found in profiles")
        return self

    def get_profile(self, name: str | None = None) -> RubricProfile:
        key = name or self.default_profile
        if key not in self.profiles:
            raise ValueError(f"Unknown rubric profile '{key}'")
        return self.profiles[key]


def load_rubric_config(path: str | Path) -> RubricConfig:
    path = Path(path)
    if not path.exists():
        raise RubricConfigError(f"Rubric config not found at {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RubricConfigError(f"Invalid JSON in {path}: {exc}") from exc
    try:
        return RubricConfig.model_validate(raw)
    except ValidationError as exc:
        raise RubricConfigError(f"Invalid rubric config in {path}: {exc}") from exc
