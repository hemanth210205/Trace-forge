import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class HypothesisOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    rank: int
    category: str
    explanation: str
    supporting_facts: list[str]
    contradicting_facts: list[str]
    evidence_trace_ids: list[str]
    evidence_span_ids: list[str]
    evidence_status: str
    confidence: Decimal | None
    uncertainty: str
    missing_evidence: list[str]
    recommended_experiment: str
    validation_status: str
    validated_by: str | None


class DiagnosisCaseOut(BaseModel):
    id: uuid.UUID
    result_id: uuid.UUID
    position: int
    case_key: str
    trace_id: uuid.UUID | None
    summary: str
    facts: list[dict[str, Any]]
    missing_evidence: list[str]
    hypotheses: list[HypothesisOut]


class DiagnosisSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    run_name: str
    retry_of: uuid.UUID | None
    status: str
    provider: str
    model: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    summary: str
    stats: dict[str, Any]
    category_counts: dict[str, int]


class DiagnosisDetail(DiagnosisSummary):
    cases: list[DiagnosisCaseOut]


class HypothesisDetail(HypothesisOut):
    diagnosis_id: uuid.UUID
    case_key: str
    trace_id: uuid.UUID | None
    facts: list[dict[str, Any]]
