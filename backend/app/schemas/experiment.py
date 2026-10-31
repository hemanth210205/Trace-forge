import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_run_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=180)]
    description: Annotated[str, Field(max_length=5000)] = ""
    baseline_config: dict[str, Any]
    candidate_config: dict[str, Any]
    hypothesis_id: uuid.UUID | None = None


class DecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    rationale: Annotated[str, Field(min_length=3, max_length=2000)]


class ArmOut(BaseModel):
    run_id: uuid.UUID
    config: dict[str, Any]
    run_status: str
    received_cases: int
    total_cases: int


class LinkedHypothesis(BaseModel):
    id: uuid.UUID
    diagnosis_id: uuid.UUID
    case_key: str
    category: str
    explanation: str
    evidence_status: str
    validation_status: str
    validated_by: str | None
    recommended_experiment: str = Field(description="The diagnosis's recommendation: context, not evidence.")


class DecisionOut(BaseModel):
    id: uuid.UUID
    decision: str
    rationale: str
    decided_by: str | None
    decided_at: datetime


class ExperimentOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    status: str
    source_run_id: uuid.UUID
    source_run_name: str
    dataset_id: uuid.UUID
    dataset_name: str
    dataset_version_id: uuid.UUID
    dataset_version: int
    baseline: ArmOut
    candidate: ArmOut
    config_diff: list[dict[str, Any]]
    hypothesis: LinkedHypothesis | None
    hypothesis_validation: dict[str, Any] | None = Field(description="Whether this experiment can validate the hypothesis, and why.")
    created_at: datetime
    started_at: datetime | None
    deadline_at: datetime | None
    finished_at: datetime | None
    failure_reason: str | None
    comparison: dict[str, Any]
    decisions: list[DecisionOut]


class ArmCaseOut(BaseModel):
    state: str
    score: float | None
    output: Any
    execution_error: dict[str, Any] | None
    evaluations: list[dict[str, Any]]
    trace_id: str | None
    latency_ms: float | None
    tokens: int | None
    cost_usd: float | None


class ExperimentCaseOut(BaseModel):
    case_key: str
    position: int
    input: Any
    expected_output: Any
    change: str
    evaluator_changes: list[dict[str, Any]]
    baseline: ArmCaseOut
    candidate: ArmCaseOut


class ExecutorArm(BaseModel):
    run_id: uuid.UUID
    run_status: str
    config: dict[str, Any]


class ExecutorExperiment(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    baseline: ExecutorArm
    candidate: ExecutorArm
