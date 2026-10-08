from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AIAdviceResponse(BaseModel):
    """Strict response schema for short, report-grounded observations."""

    model_config = ConfigDict(extra="forbid")

    observations: list[str] = Field(min_length=1, max_length=3)

    @field_validator("observations")
    @classmethod
    def normalize_observations(cls, observations: list[str]) -> list[str]:
        normalized = [item.strip() for item in observations]
        if any(not item or len(item) > 300 for item in normalized):
            raise ValueError("observations must be non-empty and at most 300 characters")
        return normalized


__all__ = ["AIAdviceResponse"]
