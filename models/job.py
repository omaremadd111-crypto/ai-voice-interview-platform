"""Job input and structured job analysis models."""
from pydantic import BaseModel, Field, field_validator

from models.common import ExperienceLevel


class JobInput(BaseModel):
    company_name: str
    job_title: str
    experience_level: ExperienceLevel | None = None
    description_text: str

    @field_validator("company_name", "job_title", "description_text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class JobAnalysis(BaseModel):
    job_title: str
    experience_level: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    technical_topics: list[str] = Field(default_factory=list)
    behavioral_competencies: list[str] = Field(default_factory=list)
    role_summary: str = ""
