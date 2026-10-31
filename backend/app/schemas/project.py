import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]*$"  # mirrors ck_projects_slug_format
Environment = Literal["development", "staging", "production"]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64].strip("-")
    return slug or "project"


class _ProjectFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "agent_name", check_fields=False)
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank.")
        return value


class ProjectCreate(_ProjectFields):
    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(default=None, min_length=1, max_length=64, pattern=SLUG_PATTERN)
    description: str = Field(default="", max_length=5000)
    agent_name: str = Field(min_length=1, max_length=100)
    agent_version: str | None = Field(default=None, max_length=50)
    environment: Environment = "development"
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(_ProjectFields):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, min_length=1, max_length=64, pattern=SLUG_PATTERN)
    description: str | None = Field(default=None, max_length=5000)
    agent_name: str | None = Field(default=None, min_length=1, max_length=100)
    agent_version: str | None = Field(default=None, max_length=50)
    environment: Environment | None = None
    settings: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _no_null_for_required_columns(self) -> "ProjectUpdate":
        nullable = {"agent_version"}
        for field in self.model_fields_set - nullable:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null.")
        return self


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str
    agent_name: str
    agent_version: str | None
    environment: str
    settings: dict[str, Any]
    is_demo: bool
    created_at: datetime
    updated_at: datetime
