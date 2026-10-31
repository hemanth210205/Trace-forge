import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank.")
        return value


class ApiKeyOut(BaseModel):
    """Key metadata. Never includes the secret or its hash.

    Keys have no expiry: a key is valid until ``revoked_at`` is set by an explicit revocation.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="The full API key. Returned only in this response and cannot be retrieved again.")


class ApiKeyIdentity(BaseModel):
    project_id: uuid.UUID
    project_slug: str
    key_id: uuid.UUID
    key_prefix: str
