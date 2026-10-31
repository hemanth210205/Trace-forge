import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

SpanKind = Literal["llm", "tool", "retrieval", "agent", "chain", "custom"]
Status = Literal["unset", "ok", "error"]

MAX_TRACES_PER_BATCH = 100
MAX_SPANS_PER_BATCH = 1000
MAX_PAYLOAD_FIELD_BYTES = 64 * 1024  # input / output / attributes, serialized
MAX_METADATA_BYTES = 16 * 1024

Name = Annotated[str, Field(min_length=1, max_length=200)]
ShortText = Annotated[str, Field(max_length=200)]


def _json_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str).encode())


def _check_size(value: Any, limit: int, field: str) -> Any:
    if value is not None and _json_size(value) > limit:
        raise ValueError(f"{field} exceeds {limit} bytes when serialized; truncate it client-side.")
    return value


class _Timed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _ordered(self) -> "_Timed":
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at must not be before started_at.")
        return self


class TraceIn(_Timed):
    id: uuid.UUID
    name: Name
    status: Status = "unset"
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(default_factory=list, max_length=20)
    user_id: ShortText | None = None
    session_id: ShortText | None = None

    @field_validator("input", "output")
    @classmethod
    def _payload_size(cls, value: Any) -> Any:
        return _check_size(value, MAX_PAYLOAD_FIELD_BYTES, "payload")

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_size(value, MAX_METADATA_BYTES, "metadata")


class SpanError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Annotated[str, Field(max_length=200)]
    message: Annotated[str, Field(max_length=4000)] = ""
    stacktrace: Annotated[str, Field(max_length=16000)] | None = None


class SpanIn(_Timed):
    id: uuid.UUID
    trace_id: uuid.UUID
    parent_span_id: uuid.UUID | None = None
    name: Name
    kind: SpanKind = "custom"
    status: Status = "unset"
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    error: SpanError | None = None
    model: ShortText | None = None
    prompt_tokens: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    completion_tokens: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    total_tokens: Annotated[int, Field(ge=0, le=200_000_000)] | None = None
    cost_usd: Annotated[Decimal, Field(ge=0, le=1_000_000, max_digits=14, decimal_places=6)] | None = None

    @field_validator("input", "output", "attributes")
    @classmethod
    def _payload_size(cls, value: Any) -> Any:
        return _check_size(value, MAX_PAYLOAD_FIELD_BYTES, "payload")

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_size(value, MAX_METADATA_BYTES, "metadata")

    @model_validator(mode="after")
    def _not_own_parent(self) -> "SpanIn":
        if self.parent_span_id == self.id:
            raise ValueError("A span cannot be its own parent.")
        return self


class IngestBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traces: list[TraceIn] = Field(default_factory=list, max_length=MAX_TRACES_PER_BATCH)
    spans: list[SpanIn] = Field(default_factory=list, max_length=MAX_SPANS_PER_BATCH)

    @model_validator(mode="after")
    def _not_empty(self) -> "IngestBatch":
        if not self.traces and not self.spans:
            raise ValueError("A batch must contain at least one trace or span.")
        return self


class IngestResult(BaseModel):
    accepted_traces: int
    accepted_spans: int


# --- Console read models ----------------------------------------------------------------------------------


class TraceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    duration_ms: float | None
    span_count: int
    error_count: int
    total_tokens: int
    total_cost_usd: Decimal
    tags: list[str]
    user_id: str | None
    session_id: str | None
    has_trace_record: bool


class TracePage(BaseModel):
    items: list[TraceSummary]
    next_cursor: str | None


class SpanOut(BaseModel):
    id: uuid.UUID
    trace_id: uuid.UUID
    parent_span_id: uuid.UUID | None
    name: str
    kind: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    duration_ms: float | None
    input: Any
    output: Any
    metadata: dict[str, Any]
    attributes: dict[str, Any]
    error: dict[str, Any] | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: Decimal | None


class TraceDetail(TraceSummary):
    input: Any
    output: Any
    metadata: dict[str, Any]
    spans: list[SpanOut]
