import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Stethoscope } from "lucide-react";

import { api, ApiError } from "../api/client";
import type { DiagnosisCase, DiagnosisDetail, DiagnosisStatus, DiagnosisSummary, EvidenceStatus, Fact, Hypothesis } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, Card, EmptyState, ErrorState, SectionTitle, Spinner, cx } from "../components/ui";
import { formatDuration, formatRelative, formatTimestamp } from "../lib/format";

const ACTIVE = new Set<DiagnosisStatus>(["pending", "running"]);

export const CATEGORY_LABEL: Record<string, string> = {
  retrieval_failure: "Retrieval failure",
  context_selection_failure: "Context selection failure",
  grounding_failure: "Grounding failure",
  tool_execution_failure: "Tool execution failure",
  tool_selection_failure: "Tool selection failure",
  reasoning_failure: "Reasoning / synthesis failure",
  output_format_failure: "Output format failure",
  insufficient_evidence: "Insufficient evidence",
};

const EVIDENCE_STYLE: Record<EvidenceStatus, { box: string; badge: string; label: string; help: string }> = {
  supported: {
    box: "border-emerald-500/30 bg-emerald-950/15",
    badge: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
    label: "Evidence supports",
    help: "The persisted evidence matches this cause's pattern. It is still a hypothesis until an experiment validates it.",
  },
  unsupported: {
    box: "border-border bg-surface",
    badge: "border-border bg-surface-raised text-slate-400",
    label: "Not established",
    help: "Plausible, but the evidence neither confirms nor rules it out.",
  },
  contradicted: {
    box: "border-border opacity-80 bg-surface/50",
    badge: "border-red-500/30 bg-red-500/10 text-red-400",
    label: "Evidence contradicts",
    help: "The persisted evidence points against this cause.",
  },
  inconclusive: {
    box: "border-amber-500/30 bg-amber-950/15",
    badge: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    label: "Inconclusive",
    help: "The evidence needed to judge this is missing.",
  },
};

function DiagnosisStatusBadge({ status }: { status: DiagnosisStatus }) {
  const style = {
    pending: "border-border bg-surface-raised text-slate-400",
    running: "border-sky-500/30 bg-sky-500/10 text-sky-400 animate-pulse",
    completed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
    failed: "border-red-500/30 bg-red-500/10 text-red-400",
  }[status];
  return (
    <span className={cx("inline-flex items-center rounded border px-2 py-0.5 text-2xs font-medium capitalize", style)}>
      {status}
    </span>
  );
}

/** Starts a diagnosis of a run and opens it. Used on the evaluation run page. */
export function DiagnoseButton({ projectId, runId }: { projectId: string; runId: string }) {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function start() {
    setBusy(true);
    setError(null);
    try {
      const d = await api<DiagnosisSummary>(`/projects/${projectId}/eval-runs/${runId}/diagnoses`, { method: "POST" });
      navigate(`/projects/${projectId}/diagnoses/${d.id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }
  return (
    <span className="inline-flex items-center gap-2">
      <Button variant="primary" size="sm" onClick={() => void start()} disabled={busy}>
        {busy ? "Starting…" : "Diagnose failures"}
      </Button>
      {error && <span role="alert" className="text-xs text-red-400 font-medium">{error}</span>}
    </span>
  );
}

export function DiagnosesPage() {
  const { projectId = "" } = useParams();
  const list = useApi((signal) => api<DiagnosisSummary[]>(`/projects/${projectId}/diagnoses`, { signal }), [projectId]);
  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between border-b border-border pb-3">
        <div>
          <div className="flex items-center gap-2">
            <Stethoscope className="h-5 w-5 text-slate-500" />
            <h1 className="text-xl font-semibold tracking-tight text-slate-100">Diagnoses</h1>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Why evaluation cases failed, backed by evidence from their traces. Start one from a scored run.
          </p>
        </div>
      </div>

      {list.error && <ErrorState error={list.error} onRetry={list.reload} />}
      {list.loading && !list.data && (
        <div className="py-12 text-center">
          <Spinner label="Loading diagnoses" />
        </div>
      )}
      {list.data && list.data.length === 0 && (
        <EmptyState
          title="No diagnoses yet"
          action={
            <Link className="text-sm underline text-accent" to={`/projects/${projectId}/evaluations`}>
              Go to evaluation runs
            </Link>
          }
        />
      )}
      {list.data && list.data.length > 0 && (
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface shadow-sm">
          {list.data.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-x-4 gap-y-1.5 p-3 text-sm hover:bg-surface-hover/80 transition-colors">
              <DiagnosisStatusBadge status={d.status} />
              <Link to={`/projects/${projectId}/diagnoses/${d.id}`} className="font-medium text-slate-100 hover:text-accent-400 transition-colors">
                {d.run_name}
              </Link>
              <span className="text-xs text-slate-400">
                {Object.entries(d.category_counts).map(([c, n]) => `${CATEGORY_LABEL[c] ?? c} ×${n}`).join(" · ") ||
                  (d.status === "completed" ? "no supported hypothesis" : "")}
              </span>
              <span className="ml-auto text-xs text-slate-500 font-mono" title={formatTimestamp(d.created_at)}>
                {d.provider}{d.model ? ` · ${d.model}` : ""} · {formatRelative(d.created_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function DiagnosisPage() {
  const { projectId = "", diagnosisId = "" } = useParams();
  const navigate = useNavigate();
  const base = `/projects/${projectId}/diagnoses/${diagnosisId}`;
  const diagnosis = useApi((signal) => api<DiagnosisDetail>(base, { signal }), [base]);
  const active = diagnosis.data ? ACTIVE.has(diagnosis.data.status) : false;
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<Error>();

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(diagnosis.reload, 2000);
    return () => clearInterval(timer);
  }, [active, diagnosis.reload]);

  if (diagnosis.error) {
    return diagnosis.error instanceof ApiError && diagnosis.error.status === 404 ? (
      <EmptyState title="Diagnosis not found" />
    ) : (
      <ErrorState error={diagnosis.error} onRetry={diagnosis.reload} />
    );
  }
  if (!diagnosis.data) {
    return (
      <div className="py-12 text-center">
        <Spinner label="Loading diagnosis" />
      </div>
    );
  }
  const d = diagnosis.data;

  async function retry() {
    setRetrying(true);
    setRetryError(undefined);
    try {
      const next = await api<DiagnosisSummary>(`${base}/retry`, { method: "POST" });
      navigate(`/projects/${projectId}/diagnoses/${next.id}`);
    } catch (err) {
      setRetryError(err as Error);
    } finally {
      setRetrying(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <Link
          to={`/projects/${projectId}/evaluations/${d.run_id}`}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-400 hover:text-slate-100 transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          ← Run {d.run_name}
        </Link>
      </div>

      <Card className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <DiagnosisStatusBadge status={d.status} />
          <h1 className="text-lg font-bold text-slate-100 font-mono">Diagnosis of {d.run_name}</h1>
          {!active && (
            <Button variant="secondary" size="sm" className="ml-auto" disabled={retrying} onClick={() => void retry()}>
              {retrying ? "Starting…" : "Run again"}
            </Button>
          )}
        </div>
        {retryError && (
          <div className="mt-2">
            <ErrorState error={retryError} />
          </div>
        )}
        <p className="text-xs text-slate-400 font-mono">
          Provider <span className="text-slate-200 font-semibold">{d.provider}</span>
          {d.model && <> · model <span className="text-slate-200">{d.model}</span></>}
          {" · "}started {formatRelative(d.created_at)}
          {d.stats.duration_ms !== undefined && <> · took {formatDuration(d.stats.duration_ms)}</>}
          {d.stats.provider_calls !== undefined && <> · {d.stats.provider_calls} provider call(s), {d.stats.provider_retries ?? 0} retried</>}
          {d.retry_of && (
            <>
              {" · "}
              <Link className="underline text-accent" to={`/projects/${projectId}/diagnoses/${d.retry_of}`}>
                previous diagnosis
              </Link>
            </>
          )}
        </p>
        {active && (
          <p className="mt-2">
            <Spinner label={d.status === "pending" ? "Queued" : "Analyzing evidence"} />
          </p>
        )}
        {d.status === "failed" && (
          <p role="alert" className="rounded border border-red-500/30 bg-red-950/20 p-2.5 text-xs text-red-200">
            Diagnosis failed: {d.error}
          </p>
        )}
        {d.summary && <p className="mt-2 text-xs text-slate-200 leading-relaxed font-sans">{d.summary}</p>}
        {(d.stats.skipped_cases ?? 0) > 0 && (
          <p className="mt-1 text-xs text-amber-400 font-medium">
            {d.stats.skipped_cases} failed case(s) were not analyzed because of the case limit.
          </p>
        )}
      </Card>

      {d.status === "completed" && (
        <section className="grid gap-3 rounded-lg border border-border bg-surface p-3 text-xs sm:grid-cols-3" aria-label="How to read this">
          <Legend title="Observed facts" tone="border-l-slate-400">
            Computed from the stored evaluation results and traces. Each links to its source span.
          </Legend>
          <Legend title="Hypotheses" tone="border-l-accent-500">
            Possible causes, each assessed against those facts. Confidence is capped by the evidence.
          </Legend>
          <Legend title="Validated causes" tone="border-l-emerald-500">
            Causes confirmed by an experiment. None exist until an experiment is run.
          </Legend>
        </section>
      )}

      {d.cases.map((c) => (
        <CaseSection key={c.id} projectId={projectId} runId={d.run_id} item={c} />
      ))}
    </div>
  );
}

function Legend({ title, tone, children }: { title: string; tone: string; children: ReactNode }) {
  return (
    <div className={cx("border-l-2 pl-3 py-0.5", tone)}>
      <p className="font-semibold text-slate-200">{title}</p>
      <p className="text-2xs text-slate-400 mt-0.5">{children}</p>
    </div>
  );
}

function spanLink(projectId: string, traceId: string | null, spanId?: string | null): string | null {
  if (!traceId) return null;
  return `/projects/${projectId}/traces/${traceId}${spanId ? `?span=${spanId}` : ""}`;
}

function CaseSection({ projectId, runId, item }: { projectId: string; runId: string; item: DiagnosisCase }) {
  const facts = new Map(item.facts.map((f) => [f.id, f]));
  const validated = item.hypotheses.filter((h) => h.validation_status === "validated");
  return (
    <section className="rounded-lg border border-border bg-surface shadow-sm overflow-hidden" aria-label={`Diagnosis of case ${item.case_key}`}>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-surface-raised px-4 py-2.5">
        <h2 className="font-mono text-sm font-semibold text-slate-100">{item.case_key}</h2>
        {item.trace_id ? (
          <Link to={spanLink(projectId, item.trace_id)!} className="text-xs text-accent-400 underline font-medium">
            Open trace →
          </Link>
        ) : (
          <span className="text-xs text-amber-400 font-medium">no trace linked</span>
        )}
      </header>
      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
        <div className="border-b border-border p-4 lg:border-b-0 lg:border-r">
          <SectionTitle>Observed facts</SectionTitle>
          <ol className="space-y-2 pt-1" aria-label="Observed facts">
            {item.facts.map((f) => (
              <FactRow key={f.id} projectId={projectId} fact={f} />
            ))}
          </ol>
          {item.missing_evidence.length > 0 && (
            <div className="mt-4 border-t border-border pt-3">
              <SectionTitle>Missing evidence</SectionTitle>
              <ul className="list-disc space-y-1 pl-5 text-xs text-amber-300 font-sans">
                {item.missing_evidence.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
        <div className="space-y-3 p-4">
          <SectionTitle>Hypotheses</SectionTitle>
          {item.hypotheses.length === 0 && <p className="text-xs italic text-slate-500">No hypothesis was proposed.</p>}
          {item.hypotheses.map((h) => (
            <HypothesisCard key={h.id} projectId={projectId} runId={runId} hypothesis={h} facts={facts} />
          ))}
          <div className="border-t border-border pt-3">
            <SectionTitle>Validated causes</SectionTitle>
            {validated.length === 0 ? (
              <p className="text-xs text-slate-400">None. No cause for this case has been confirmed by an experiment.</p>
            ) : (
              <ul className="text-xs space-y-1">
                {validated.map((h) => (
                  <li key={h.id} className="font-medium text-emerald-400">
                    {CATEGORY_LABEL[h.category]} — validated by{" "}
                    {h.validated_by?.startsWith("experiment:") ? (
                      <Link className="underline text-accent-400" to={`/projects/${projectId}/experiments/${h.validated_by.slice(11)}`}>
                        an experiment
                      </Link>
                    ) : (
                      h.validated_by
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function FactRow({ projectId, fact }: { projectId: string; fact: Fact }) {
  const link = spanLink(projectId, fact.trace_id, fact.span_id);
  return (
    <li className="grid grid-cols-[2.25rem_minmax(0,1fr)] gap-2 text-xs" id={`fact-${fact.id}`}>
      <span className="font-mono text-slate-500 font-medium">{fact.id}</span>
      <span className="min-w-0 break-words text-slate-300">
        <span className="mr-1.5 rounded border border-border bg-surface-raised px-1.5 py-0.2 font-mono text-2xs uppercase text-slate-400">
          {fact.kind.replace("_", " ")}
        </span>
        {fact.statement}
        {link && fact.span_id && (
          <>
            {" "}
            <Link to={link} className="whitespace-nowrap underline text-accent-400 font-mono text-2xs">
              span {fact.span_id.slice(0, 8)}
            </Link>
          </>
        )}
      </span>
    </li>
  );
}

function FactChips({ ids, facts, tone }: { ids: string[]; facts: Map<string, Fact>; tone: string }) {
  return (
    <span className="flex flex-wrap gap-1">
      {ids.map((id) => (
        <a
          key={id}
          href={`#fact-${id}`}
          title={facts.get(id)?.statement}
          className={cx("rounded border px-1.5 font-mono text-2xs font-medium transition-colors", tone)}
        >
          {id}
        </a>
      ))}
    </span>
  );
}

function HypothesisCard({
  projectId,
  runId,
  hypothesis: h,
  facts,
}: {
  projectId: string;
  runId: string;
  hypothesis: Hypothesis;
  facts: Map<string, Fact>;
}) {
  const style = EVIDENCE_STYLE[h.evidence_status];
  const confidence = h.confidence === null ? null : Number(h.confidence);
  const traceId = h.evidence_trace_ids[0] ?? null;
  return (
    <article className={cx("rounded-lg border p-3 text-xs", style.box)} aria-label={`Hypothesis ${CATEGORY_LABEL[h.category] ?? h.category}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-slate-100">{CATEGORY_LABEL[h.category] ?? h.category}</span>
        <span className={cx("rounded border px-1.5 py-0.5 text-2xs font-medium", style.badge)} title={style.help}>
          {style.label}
        </span>
        <span className="rounded border border-border bg-surface px-1.5 py-0.5 text-2xs text-slate-400" title="Only an experiment can validate a cause">
          {h.validation_status === "validated" ? "Validated" : "Unvalidated hypothesis"}
        </span>
        <span className="ml-auto font-mono tabular-nums text-slate-300" title="Provider confidence, capped by the evidence status">
          {confidence === null ? "confidence n/a" : `confidence ${confidence.toFixed(2)}`}
        </span>
      </div>
      <p className="mt-2 text-xs text-slate-200 leading-relaxed font-sans">{h.explanation}</p>
      <dl className="mt-2.5 grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-2 gap-y-1.5 text-2xs">
        {h.supporting_facts.length > 0 && (
          <>
            <dt className="text-slate-400 font-medium">Supported by</dt>
            <dd>
              <FactChips ids={h.supporting_facts} facts={facts} tone="border-emerald-500/30 bg-emerald-500/10 text-emerald-400" />
            </dd>
          </>
        )}
        {h.contradicting_facts.length > 0 && (
          <>
            <dt className="text-slate-400 font-medium">Contradicted by</dt>
            <dd>
              <FactChips ids={h.contradicting_facts} facts={facts} tone="border-red-500/30 bg-red-500/10 text-red-400" />
            </dd>
          </>
        )}
        {h.evidence_span_ids.length > 0 && (
          <>
            <dt className="text-slate-400 font-medium">Evidence spans</dt>
            <dd className="flex flex-wrap gap-x-2 font-mono">
              {h.evidence_span_ids.map((s) => (
                <Link key={s} to={spanLink(projectId, traceId, s)!} className="underline text-accent-400">
                  {s.slice(0, 8)}
                </Link>
              ))}
            </dd>
          </>
        )}
        {h.uncertainty && (
          <>
            <dt className="text-slate-400 font-medium">Uncertainty</dt>
            <dd className="text-slate-300 font-sans">{h.uncertainty}</dd>
          </>
        )}
        {h.missing_evidence.length > 0 && (
          <>
            <dt className="text-slate-400 font-medium">Missing</dt>
            <dd className="text-amber-300 font-sans">{h.missing_evidence.join(" ")}</dd>
          </>
        )}
        {h.recommended_experiment && (
          <>
            <dt className="text-slate-400 font-medium">Recommended experiment</dt>
            <dd className="text-slate-300 font-sans">
              {h.recommended_experiment} <span className="text-slate-500">(a recommendation, not evidence)</span>
            </dd>
          </>
        )}
      </dl>
      {h.validation_status === "unvalidated" && h.category !== "insufficient_evidence" && (
        <Link
          className="mt-3 inline-flex items-center gap-1 text-xs text-accent-400 hover:underline font-medium"
          to={`/projects/${projectId}/experiments/new?source_run=${runId}&hypothesis=${h.id}`}
        >
          Test with an experiment →
        </Link>
      )}
    </article>
  );
}
