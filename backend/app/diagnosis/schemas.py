"""Typed data passed between workflow steps and to/from the LLM provider."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "retrieval_failure",
    "context_selection_failure",
    "grounding_failure",
    "tool_execution_failure",
    "tool_selection_failure",
    "reasoning_failure",
    "output_format_failure",
    "insufficient_evidence",
]
EvidenceStatus = Literal["supported", "unsupported", "contradicted", "inconclusive"]
FactKind = Literal["evaluation", "execution_error", "trace", "retrieval", "tool", "llm", "comparison", "evidence_gap"]

ANSWER_EVALUATORS = frozenset({"exact_match", "normalized_match", "contains", "json_match"})
FORMAT_EVALUATORS = frozenset({"required_fields"})


class Fact(BaseModel):
    """An observation computed from persisted records. ``trace_id``/``span_id`` are copied from those records,
    never from model output, so every reference resolves."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: FactKind
    statement: str
    trace_id: str | None = None
    span_id: str | None = None


class Signals(BaseModel):
    """Deterministic, machine-checkable summary of a case's evidence (used by assessment, not by the UI)."""

    has_trace: bool = False
    answer_failed: bool = False
    answer_passed_any: bool = False
    format_failed: bool = False
    source_failed: bool = False
    expected_sources: list[str] = Field(default_factory=list)
    missing_sources: list[str] = Field(default_factory=list)
    retrieved_expected: dict[str, int] = Field(default_factory=dict)  # source -> rank (1-based)
    retrieval_span_count: int = 0
    retrieved_document_count: int = 0
    documents_have_content: bool = False
    best_document: str | None = None
    best_document_overlap: float | None = None
    expected_document_overlap: float | None = None
    tool_span_count: int = 0
    tool_error_span_ids: list[str] = Field(default_factory=list)
    execution_error: bool = False
    expected_tool: str | None = None
    actual_tools: list[str] = Field(default_factory=list)
    tool_selection_mismatch: bool = False
    tool_selection_matched: bool = False
    tool_selection_ambiguous: bool = False
    # Claim-level attribution (app.diagnosis.claims). In-memory only: Signals are never persisted.
    answer_available: bool = True
    tool_evidence: bool = False
    claim_count: int = 0
    claims_supported: int = 0
    claims_supported_by_expected: int = 0
    claims_supported_by_other_document: int = 0
    claims_supported_by_tool: int = 0
    claims_contradicted: int = 0
    claims_unsupported: int = 0
    claims_abstention: int = 0
    claims_undetermined: int = 0
    supporting_sources: list[str] = Field(default_factory=list)
    contradicting_sources: list[str] = Field(default_factory=list)
    # Which fact ids back each signal, so assessment can cite them.
    fact_refs: dict[str, list[str]] = Field(default_factory=dict)


class CaseEvidence(BaseModel):
    result_id: str
    case_key: str
    trace_id: str | None
    facts: list[Fact]
    signals: Signals
    missing_evidence: list[str]


# --- provider contract -----------------------------------------------------------------------------------------

ShortText = Annotated[str, Field(max_length=2000)]


class ProposedHypothesis(BaseModel):
    """What a provider may say. It can only cite fact ids; trace/span ids are resolved server-side."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    explanation: ShortText
    supporting_fact_ids: Annotated[list[Annotated[str, Field(max_length=16)]], Field(max_length=20)]
    contradicting_fact_ids: Annotated[list[Annotated[str, Field(max_length=16)]], Field(max_length=20)] = []
    confidence: Annotated[float, Field(ge=0, le=1)]
    uncertainty: ShortText = ""
    missing_evidence: Annotated[list[Annotated[str, Field(max_length=500)]], Field(max_length=10)] = []
    recommended_experiment: ShortText = ""


class ProviderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: ShortText
    hypotheses: Annotated[list[ProposedHypothesis], Field(max_length=6)]


# --- assessed result ---------------------------------------------------------------------------------------------


class AssessedHypothesis(BaseModel):
    category: Category
    explanation: str
    supporting_facts: list[str]
    contradicting_facts: list[str]
    evidence_trace_ids: list[str]
    evidence_span_ids: list[str]
    evidence_status: EvidenceStatus
    confidence: float | None
    uncertainty: str
    missing_evidence: list[str]
    recommended_experiment: str


class CaseDiagnosisResult(BaseModel):
    evidence: CaseEvidence
    summary: str
    hypotheses: list[AssessedHypothesis]
    discarded_references: int = 0


class WorkflowStats(BaseModel):
    steps: list[str] = Field(default_factory=list)
    failed_cases: int = 0
    analyzed_cases: int = 0
    skipped_cases: int = 0
    provider_calls: int = 0
    provider_retries: int = 0
    discarded_references: int = 0
    duration_ms: float = 0.0


class WorkflowState(BaseModel):
    """Explicit state threaded through the steps of one diagnosis."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    diagnosis_id: str
    project_id: str
    run_id: str
    run: dict[str, Any] | None = None
    failed_results: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[CaseEvidence] = Field(default_factory=list)
    proposals: dict[str, ProviderOutput] = Field(default_factory=dict)  # result_id -> provider output
    results: list[CaseDiagnosisResult] = Field(default_factory=list)
    summary: str = ""
    stats: WorkflowStats = Field(default_factory=WorkflowStats)
