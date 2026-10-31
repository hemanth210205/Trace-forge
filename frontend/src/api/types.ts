// Mirrors the backend response models (backend/app/schemas). Decimal fields arrive as strings.

export type Status = "ok" | "error" | "unset";
export type SpanKind = "llm" | "tool" | "retrieval" | "agent" | "chain" | "custom";

export interface User {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
}

export interface AuthResponse {
  user: User;
  csrf_token: string;
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string;
  agent_name: string;
  agent_version: string | null;
  environment: string;
  settings: Record<string, unknown>;
  is_demo: boolean;
  created_at: string;
  updated_at: string;
}

export interface ApiKey {
  id: string;
  project_id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export interface TraceSummary {
  id: string;
  name: string;
  status: Status;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  span_count: number;
  error_count: number;
  total_tokens: number;
  total_cost_usd: string;
  tags: string[];
  user_id: string | null;
  session_id: string | null;
  has_trace_record: boolean;
}

export interface TracePage {
  items: TraceSummary[];
  next_cursor: string | null;
}

export interface SpanError {
  type: string;
  message: string;
  stacktrace: string | null;
}

export interface Span {
  id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  kind: SpanKind;
  status: Status;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  input: unknown;
  output: unknown;
  metadata: Record<string, unknown>;
  attributes: Record<string, unknown>;
  error: SpanError | null;
  model: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  cost_usd: string | null;
}

export interface TraceDetail extends TraceSummary {
  input: unknown;
  output: unknown;
  metadata: Record<string, unknown>;
  spans: Span[];
}

// --- M5: datasets and evaluations ----------------------------------------------------------------------

export interface VersionSummary {
  id: string;
  version: number;
  case_count: number;
  content_hash: string;
  notes: string;
  created_at: string;
}

export interface Dataset {
  id: string;
  project_id: string;
  name: string;
  description: string;
  metadata: Record<string, unknown>;
  draft_case_count: number;
  latest_version: VersionSummary | null;
  draft_changed_since_latest: boolean;
  created_at: string;
  updated_at: string;
}

export interface DatasetCase {
  id: string;
  key: string;
  position: number;
  input: unknown;
  expected_output: unknown;
  expected_sources: string[];
  metadata: Record<string, unknown>;
}

export interface VersionDetail extends VersionSummary {
  dataset_id: string;
  cases: DatasetCase[];
}

export type RunStatus = "pending" | "running" | "scoring" | "completed" | "incomplete" | "failed";

export interface EvaluatorStats {
  passed: number;
  failed: number;
  not_applicable: number;
  pass_rate: number | null;
}

export interface EvaluationRun {
  id: string;
  project_id: string;
  dataset_id: string;
  dataset_name: string;
  dataset_version_id: string;
  dataset_version: number;
  name: string;
  status: RunStatus;
  evaluators: Record<string, unknown>[];
  total_cases: number;
  created_at: string;
  started_at: string | null;
  scoring_started_at: string | null;
  finished_at: string | null;
  failure_reason: string | null;
  received_cases: number;
  summary: {
    total_cases?: number;
    received_cases?: number;
    missing_cases?: number;
    passed?: number;
    failed?: number;
    not_evaluated?: number;
    execution_errors?: number;
    mean_score?: number | null;
    evaluators?: Record<string, EvaluatorStats>;
  };
  pass_rate: number | null;
}

export interface SupportingRef {
  trace_id: string;
  span_id?: string;
  reason?: string;
}

export interface EvaluatorOutcome {
  evaluator: string;
  type: string;
  applicable: boolean;
  passed: boolean | null;
  score: number | null;
  expected: unknown;
  actual: unknown;
  explanation: string;
  supporting_refs: SupportingRef[];
}

export type CaseState = "passed" | "failed" | "not_evaluated" | "missing" | "pending";

export interface CaseResult {
  case_id: string;
  key: string;
  position: number;
  input: unknown;
  expected_output: unknown;
  expected_sources: string[];
  state: CaseState;
  output: unknown;
  execution_error: { type?: string; message?: string } | null;
  trace_id: string | null;
  latency_ms: number | null;
  score: string | null;
  evaluations: EvaluatorOutcome[];
  submitted_at: string | null;
}

// --- M6: diagnosis -------------------------------------------------------------------------------------

export type DiagnosisStatus = "pending" | "running" | "completed" | "failed";
export type EvidenceStatus = "supported" | "unsupported" | "contradicted" | "inconclusive";

export interface Fact {
  id: string;
  kind: string;
  statement: string;
  trace_id: string | null;
  span_id: string | null;
}

export interface Hypothesis {
  id: string;
  case_id: string;
  rank: number;
  category: string;
  explanation: string;
  supporting_facts: string[];
  contradicting_facts: string[];
  evidence_trace_ids: string[];
  evidence_span_ids: string[];
  evidence_status: EvidenceStatus;
  confidence: string | null;
  uncertainty: string;
  missing_evidence: string[];
  recommended_experiment: string;
  validation_status: "unvalidated" | "validated";
  validated_by: string | null;
}

export interface DiagnosisCase {
  id: string;
  result_id: string;
  position: number;
  case_key: string;
  trace_id: string | null;
  summary: string;
  facts: Fact[];
  missing_evidence: string[];
  hypotheses: Hypothesis[];
}

export interface DiagnosisSummary {
  id: string;
  project_id: string;
  run_id: string;
  run_name: string;
  retry_of: string | null;
  status: DiagnosisStatus;
  provider: string;
  model: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  summary: string;
  stats: { analyzed_cases?: number; failed_cases?: number; skipped_cases?: number; provider_calls?: number; provider_retries?: number; discarded_references?: number; duration_ms?: number; steps?: string[] };
  category_counts: Record<string, number>;
}

export interface DiagnosisDetail extends DiagnosisSummary {
  cases: DiagnosisCase[];
}

// --- M7: experiments ------------------------------------------------------------------------------------

export type ExperimentStatus = "pending" | "running" | "completed" | "failed";
export type CaseChange = "fixed" | "regressed" | "unchanged_pass" | "unchanged_fail" | "incomplete";

export interface ExperimentArm {
  run_id: string;
  config: Record<string, unknown>;
  run_status: RunStatus;
  received_cases: number;
  total_cases: number;
}

export interface ArmSummary {
  run_id: string;
  status: string;
  total_cases: number;
  passed: number;
  failed: number;
  missing: number;
  not_evaluated: number;
  pass_rate: number | null;
  evaluators: Record<string, { passed: number; failed: number; pass_rate: number | null }>;
  retrieval: { checked_cases: number; mean_recall: number | null };
  latency_ms: { measured_cases: number; mean: number | null; median: number | null };
  tokens: { measured_cases: number; total: number | null };
  cost_usd: { measured_cases: number; total: number | null };
}

export interface PairedMeasurement {
  paired_cases: number;
  available: boolean;
  baseline_mean: number | null;
  candidate_mean: number | null;
  delta: number | null;
}

export interface ExperimentComparison {
  arms: { baseline: ArmSummary; candidate: ArmSummary };
  pass_rate_delta: number | null;
  cases: Record<CaseChange, string[]>;
  evaluator_changes: { case: string; evaluator: string; change: "fixed" | "regressed" }[];
  measurements: { latency_ms: PairedMeasurement; tokens: PairedMeasurement; cost_usd: PairedMeasurement };
  suggested_outcome: "candidate_improves" | "candidate_regresses" | "mixed" | "no_change" | "inconclusive";
  suggested_outcome_reason: string;
}

export interface Experiment {
  id: string;
  project_id: string;
  name: string;
  description: string;
  status: ExperimentStatus;
  source_run_id: string;
  source_run_name: string;
  dataset_id: string;
  dataset_name: string;
  dataset_version_id: string;
  dataset_version: number;
  baseline: ExperimentArm;
  candidate: ExperimentArm;
  config_diff: { path: string; change: "added" | "removed" | "changed"; baseline: unknown; candidate: unknown }[];
  hypothesis: {
    id: string; diagnosis_id: string; case_key: string; category: string; explanation: string; evidence_status: EvidenceStatus;
    validation_status: "unvalidated" | "validated"; validated_by: string | null; recommended_experiment: string;
  } | null;
  hypothesis_validation: { eligible: boolean; reason: string } | null;
  created_at: string;
  started_at: string | null;
  deadline_at: string | null;
  finished_at: string | null;
  failure_reason: string | null;
  comparison: Partial<ExperimentComparison>;
  decisions: { id: string; decision: "approve" | "reject"; rationale: string; decided_by: string | null; decided_at: string }[];
}

export interface ArmCase {
  state: CaseState;
  score: number | null;
  output: unknown;
  execution_error: Record<string, unknown> | null;
  evaluations: EvaluatorOutcome[];
  trace_id: string | null;
  latency_ms: number | null;
  tokens: number | null;
  cost_usd: number | null;
}

export interface ExperimentCase {
  case_key: string;
  position: number;
  input: unknown;
  expected_output: unknown;
  change: CaseChange;
  evaluator_changes: { evaluator: string; change: "fixed" | "regressed" }[];
  baseline: ArmCase;
  candidate: ArmCase;
}
