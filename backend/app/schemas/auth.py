import uuid
from datetime import datetime

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, field_validator

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256  # bounds argon2 work per request


def normalize_email(value: str) -> str:
    """Trims and lowercases the whole address; the database CHECK requires ``email = lower(email)``."""
    return value.strip().lower()


def canonical_email(value: str) -> str:
    """The form registration stores (Unicode NFC, canonical domain, lowercased), so login matches it.

    Strings that are not valid addresses are only lowercased: login then fails with the usual 401, not a 422 that
    would reveal anything about the input.
    """
    try:
        return normalize_email(validate_email(value.strip(), check_deliverability=False).normalized)
    except EmailNotValidError:
        return normalize_email(value)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320, examples=["ada@example.com"])
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        try:
            validate_email(value.strip(), check_deliverability=False)
        except EmailNotValidError:
            raise ValueError("Invalid email address.") from None
        return canonical_email(value)

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Display name must not be blank.")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return canonical_email(value)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserOut
    csrf_token: str = Field(description="Send as the X-CSRF-Token header on state-changing requests.")
