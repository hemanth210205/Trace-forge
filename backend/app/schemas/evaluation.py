import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_JSON_BYTES = 64 * 1024
MAX_METADATA_BYTES = 16 * 1024
CaseKey = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$")]
SourceRef = Annotated[str, Field(min_length=1, max_length=500)]


def _bounded(value: Any, limit: int, what: str) -> Any:
    if value is not None and len(json.dumps(value, ensure_ascii=False, default=str).encode()) > limit:
        raise ValueError(f"{what} exceeds {limit} bytes when serialized.")
    return value


class _CaseFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("input", "expected_output", check_fields=False)
    @classmethod
    def _payload_size(cls, value: Any) -> Any:
        return _bounded(value, MAX_JSON_BYTES, "value")

    @field_validator("metadata", check_fields=False)
    @classmethod
    def _metadata_size(cls, value: Any) -> Any:
        return _bounded(value, MAX_METADATA_BYTES, "metadata")


class CaseCreate(_CaseFields):
    key: CaseKey | None = Field(default=None, description="Stable case identifier; generated if omitted.")
    input: Any = Field(description="Input passed to the agent (any JSON value except null).")
    expected_output: Any = None
    expected_sources: Annotated[list[SourceRef], Field(max_length=50)] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input")
    @classmethod
    def _input_required(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("input is required.")
        return value


class CaseUpdate(_CaseFields):
    key: CaseKey | None = None
    input: Any = None
    expected_output: Any = None
    expected_sources: Annotated[list[SourceRef], Field(max_length=50)] | None = None
    metadata: dict[str, Any] | None = None


class CaseOut(BaseModel):
    id: uuid.UUID
    key: str
    position: int
    input: Any
    expected_output: Any
    expected_sources: list[str]
    metadata: dict[str, Any]


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=5000)] = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank.")
        return value

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded(value, MAX_METADATA_BYTES, "metadata")


class DatasetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    description: Annotated[str, Field(max_length=5000)] | None = None
    metadata: dict[str, Any] | None = None


class VersionSummary(BaseModel):
    id: uuid.UUID
    version: int
    case_count: int
    content_hash: str
    notes: str
    created_at: datetime


class DatasetOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    metadata: dict[str, Any]
    draft_case_count: int
    latest_version: VersionSummary | None
    draft_changed_since_latest: bool
    created_at: datetime
    updated_at: datetime


class VersionDetail(VersionSummary):
    dataset_id: uuid.UUID
    cases: list[CaseOut]


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: Annotated[str, Field(max_length=2000)] = ""


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["json", "jsonl"]
    content: Annotated[str, Field(min_length=1, max_length=4_000_000)]
    mode: Literal["append", "replace"] = "append"


class ImportResult(BaseModel):
    imported: int
    draft_case_count: int


# --- evaluation runs -----------------------------------------------------------------------------------------


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=200)]
    evaluators: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=20)]


class SdkRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: Annotated[str, Field(min_length=1, max_length=100)]
    version: Annotated[int, Field(ge=1)] | None = Field(default=None, description="Defaults to the latest version.")
    name: Annotated[str, Field(min_length=1, max_length=200)]
    evaluators: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=20)]


class RunOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_name: str
    dataset_version_id: uuid.UUID
    dataset_version: int
    name: str
    status: str
    evaluators: list[dict[str, Any]]
    total_cases: int
    created_at: datetime
    started_at: datetime | None
    scoring_started_at: datetime | None
    finished_at: datetime | None
    failure_reason: str | None
    received_cases: int
    summary: dict[str, Any]
    pass_rate: float | None = Field(description="Only for completed runs; null otherwise.")


class ExecutorCase(BaseModel):
    """What an executor sees: no expected output or sources, so answers cannot leak into the agent."""

    case_id: uuid.UUID
    key: str
    input: Any
    metadata: dict[str, Any]


class ExecutorRun(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    dataset_name: str
    dataset_version: int
    cases: list[ExecutorCase]


class ResultSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: uuid.UUID
    output: Any = None
    error: dict[str, Annotated[str, Field(max_length=4000)]] | None = Field(
        default=None, description="Set when the task raised: {'type': ..., 'message': ...}."
    )
    trace_id: uuid.UUID | None = None
    latency_ms: Annotated[float, Field(ge=0, le=86_400_000)] | None = None

    @field_validator("output")
    @classmethod
    def _size(cls, value: Any) -> Any:
        return _bounded(value, MAX_JSON_BYTES, "output")


class ResultsSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: Annotated[list[ResultSubmission], Field(min_length=1, max_length=200)]


class SubmitAck(BaseModel):
    accepted: int
    status: str
    received_cases: int
    total_cases: int


class CaseResultOut(BaseModel):
    case_id: uuid.UUID
    key: str
    position: int
    input: Any
    expected_output: Any
    expected_sources: list[str]
    state: Literal["passed", "failed", "not_evaluated", "missing", "pending"]
    output: Any
    execution_error: dict[str, Any] | None
    trace_id: uuid.UUID | None = Field(description="Only when the trace exists in this project.")
    latency_ms: float | None
    score: Decimal | None
    evaluations: list[dict[str, Any]]
    submitted_at: datetime | None
