"""Candidate input, structured CV analysis, and candidate<->job fit models."""
from pydantic import BaseModel, Field, field_validator


class CandidateInput(BaseModel):
    full_name: str | None = None
    cv_text: str

    @field_validator("cv_text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("cv_text must not be blank")
        return v.strip()


class CandidateAnalysis(BaseModel):
    full_name: str | None = Field(
        default=None,
        description=(
            "Candidate name copied from an explicit CV heading or name field; null when "
            "the CV does not explicitly display one. Never infer it from email or filename."
        ),
    )
    skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    important_cv_claims: list[str] = Field(default_factory=list)
    relevant_experience: list[str] = Field(default_factory=list)
    unclear_claims_to_validate: list[str] = Field(default_factory=list)

    @field_validator("full_name")
    @classmethod
    def _normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class FitAnalysis(BaseModel):
    strong_alignment_areas: list[str] = Field(default_factory=list)
    relevant_candidate_experience: list[str] = Field(default_factory=list)
    important_job_requirements: list[str] = Field(default_factory=list)
    skills_requiring_validation: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    questions_to_investigate: list[str] = Field(default_factory=list)
