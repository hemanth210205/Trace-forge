import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { DiagnosisCase, DiagnosisDetail, DiagnosisStatus, DiagnosisSummary, EvidenceStatus, Fact, Hypothesis } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, EmptyState, ErrorState, SectionTitle, Spinner, cx } from "../components/ui";
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
  supported: { box: "border-emerald-300", badge: "border-emerald-300 bg-emerald-50 text-emerald-800", label: "Evidence supports",
    help: "The persisted evidence matches this cause's pattern. It is still a hypothesis until an experiment validates it." },
  unsupported: { box: "border-ink-200", badge: "border-ink-200 bg-ink-50 text-ink-700", label: "Not established",
    help: "Plausible, but the evidence neither confirms nor rules it out." },
  contradicted: { box: "border-ink-200 opacity-80", badge: "border-red-200 bg-red-50 text-red-800", label: "Evidence contradicts",
    help: "The persisted evidence points against this cause." },
  inconclusive: { box: "border-amber-200", badge: "border-amber-300 bg-amber-50 text-amber-900", label: "Inconclusive",
    help: "The evidence needed to judge this is missing." },
};

function DiagnosisStatusBadge({ status }: { status: DiagnosisStatus }) {
  const style = { pending: "border-ink-200 text-ink-700", running: "border-sky-300 bg-sky-50 text-sky-800",
    completed: "border-emerald-300 bg-emerald-50 text-emerald-800", failed: "border-red-300 bg-red-50 text-red-800" }[status];
  return <span className={cx("inline-flex rounded border px-1.5 font-mono text-2xs uppercase tracking-wide", style)}>{status}</span>;
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
      <Button variant="primary" onClick={() => void start()} disabled={busy}>{busy ? "Starting…" : "Diagnose failures"}</Button>
      {error && <span role="alert" className="text-xs text-red-700">{error}</span>}
    </span>
  );
}

export function DiagnosesPage() {
  const { projectId = "" } = useParams();
  const list = useApi((signal) => api<DiagnosisSummary[]>(`/projects/${projectId}/diagnoses`, { signal }), [projectId]);
  return (
    <div className="mx-auto max-w-6xl">
      <h1 className="text-lg font-semibold">Diagnoses</h1>
      <p className="mb-4 text-sm text-ink-500">Evidence-based investigations of failed evaluation cases. Start one from a scored evaluation run.</p>
      {list.error && <ErrorState error={list.error} onRetry={list.reload} />}
      {list.loading && !list.data && <Spinner label="Loading diagnoses" />}
      {list.data && list.data.length === 0 && (
        <EmptyState title="No diagnoses yet" action={<Link className="text-sm underline" to={`/projects/${projectId}/evaluations`}>Go to evaluation runs</Link>} />
      )}
      {list.data && list.data.length > 0 && (
        <ul className="divide-y divide-ink-100 border border-ink-200 bg-white">
          {list.data.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-sm">
              <DiagnosisStatusBadge status={d.status} />
              <Link to={`/projects/${projectId}/diagnoses/${d.id}`} className="font-medium hover:underline">{d.run_name}</Link>
              <span className="text-xs text-ink-500">{Object.entries(d.category_counts).map(([c, n]) => `${CATEGORY_LABEL[c] ?? c} ×${n}`).join(" · ") || (d.status === "completed" ? "no supported hypothesis" : "")}</span>
              <span className="ml-auto text-xs text-ink-500" title={formatTimestamp(d.created_at)}>{d.provider}{d.model ? ` · ${d.model}` : ""} · {formatRelative(d.created_at)}</span>
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
    return diagnosis.error instanceof ApiError && diagnosis.error.status === 404
      ? <EmptyState title="Diagnosis not found" />
      : <ErrorState error={diagnosis.error} onRetry={diagnosis.reload} />;
  }
  if (!diagnosis.data) return <Spinner label="Loading diagnosis" />;
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
    <div className="mx-auto max-w-6xl space-y-3">
      <Link to={`/projects/${projectId}/evaluations/${d.run_id}`} className="text-sm text-ink-500 hover:text-ink-900">← Run {d.run_name}</Link>
      <section className="border border-ink-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <DiagnosisStatusBadge status={d.status} />
          <h1 className="text-lg font-semibold">Diagnosis of {d.run_name}</h1>
          {!active && <Button variant="ghost" className="ml-auto" disabled={retrying} onClick={() => void retry()}>{retrying ? "Starting…" : "Run again"}</Button>}
        </div>
        {retryError && <div className="mt-2"><ErrorState error={retryError} /></div>}
        <p className="mt-1 text-xs text-ink-500">
          Provider <span className="font-mono">{d.provider}</span>{d.model && <> · model <span className="font-mono">{d.model}</span></>}
          {" · "}started {formatRelative(d.created_at)}
          {d.stats.duration_ms !== undefined && <> · took {formatDuration(d.stats.duration_ms)}</>}
          {d.stats.provider_calls !== undefined && <> · {d.stats.provider_calls} provider call(s), {d.stats.provider_retries ?? 0} retried</>}
          {d.retry_of && <> · <Link className="underline" to={`/projects/${projectId}/diagnoses/${d.retry_of}`}>previous diagnosis</Link></>}
        </p>
        {active && <p className="mt-2"><Spinner label={d.status === "pending" ? "Queued" : "Analyzing evidence"} /></p>}
        {d.status === "failed" && <p role="alert" className="mt-2 border border-red-200 bg-red-50 px-2.5 py-1.5 text-sm text-red-800">Diagnosis failed: {d.error}</p>}
        {d.summary && <p className="mt-2 text-sm">{d.summary}</p>}
        {(d.stats.skipped_cases ?? 0) > 0 && <p className="mt-1 text-xs text-amber-800">{d.stats.skipped_cases} failed case(s) were not analyzed because of the case limit.</p>}
      </section>

      {d.status === "completed" && (
        <section className="grid gap-2 border border-ink-200 bg-white px-4 py-3 text-xs sm:grid-cols-3" aria-label="How to read this">
          <Legend title="Observed facts" tone="border-l-ink-900">Computed from the stored evaluation results and traces. Each links to its source span.</Legend>
          <Legend title="Hypotheses" tone="border-l-sky-700">Possible causes, each assessed against those facts. Confidence is capped by the evidence.</Legend>
          <Legend title="Validated causes" tone="border-l-emerald-700">Causes confirmed by an experiment. None exist until an experiment is run.</Legend>
        </section>
      )}

      {d.cases.map((c) => <CaseSection key={c.id} projectId={projectId} runId={d.run_id} item={c} />)}
    </div>
  );
}

function Legend({ title, tone, children }: { title: string; tone: string; children: ReactNode }) {
  return (
    <div className={cx("border-l-4 pl-2", tone)}>
      <p className="font-semibold text-ink-900">{title}</p>
      <p className="text-ink-500">{children}</p>
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
    <section className="border border-ink-200 bg-white" aria-label={`Diagnosis of case ${item.case_key}`}>
      <header className="flex flex-wrap items-center gap-3 border-b border-ink-100 px-4 py-2.5">
        <h2 className="font-mono text-sm font-semibold">{item.case_key}</h2>
        {item.trace_id ? (
          <Link to={spanLink(projectId, item.trace_id)!} className="text-xs underline">Open trace →</Link>
        ) : (
          <span className="text-xs text-amber-800">no trace linked</span>
        )}
      </header>
      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
        <div className="border-b border-ink-100 px-4 py-3 lg:border-b-0 lg:border-r">
          <SectionTitle>Observed facts</SectionTitle>
          <ol className="space-y-1.5" aria-label="Observed facts">
            {item.facts.map((f) => <FactRow key={f.id} projectId={projectId} fact={f} />)}
          </ol>
          {item.missing_evidence.length > 0 && (
            <div className="mt-3">
              <SectionTitle>Missing evidence</SectionTitle>
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-900">{item.missing_evidence.map((m) => <li key={m}>{m}</li>)}</ul>
            </div>
          )}
        </div>
        <div className="space-y-2 px-4 py-3">
          <SectionTitle>Hypotheses</SectionTitle>
          {item.hypotheses.length === 0 && <p className="text-xs italic text-ink-500">No hypothesis was proposed.</p>}
          {item.hypotheses.map((h) => <HypothesisCard key={h.id} projectId={projectId} runId={runId} hypothesis={h} facts={facts} />)}
          <div className="border-t border-ink-100 pt-2">
            <SectionTitle>Validated causes</SectionTitle>
            {validated.length === 0 ? (
              <p className="text-xs text-ink-500">None. No cause for this case has been confirmed by an experiment.</p>
            ) : (
              <ul className="text-xs">{validated.map((h) => <li key={h.id} className="font-medium text-emerald-800">{CATEGORY_LABEL[h.category]} — validated by {h.validated_by?.startsWith("experiment:") ? <Link className="underline" to={`/projects/${projectId}/experiments/${h.validated_by.slice(11)}`}>an experiment</Link> : h.validated_by}</li>)}</ul>
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
      <span className="font-mono text-ink-500">{fact.id}</span>
      <span className="min-w-0 break-words">
        <span className="mr-1 rounded border border-ink-200 px-1 font-mono text-2xs uppercase text-ink-500">{fact.kind.replace("_", " ")}</span>
        {fact.statement}
        {link && fact.span_id && <> <Link to={link} className="whitespace-nowrap underline">span {fact.span_id.slice(0, 8)}</Link></>}
      </span>
    </li>
  );
}

function FactChips({ ids, facts, tone }: { ids: string[]; facts: Map<string, Fact>; tone: string }) {
  return (
    <span className="flex flex-wrap gap-1">
      {ids.map((id) => (
        <a key={id} href={`#fact-${id}`} title={facts.get(id)?.statement} className={cx("rounded border px-1 font-mono text-2xs", tone)}>{id}</a>
      ))}
    </span>
  );
}

function HypothesisCard({ projectId, runId, hypothesis: h, facts }: { projectId: string; runId: string; hypothesis: Hypothesis; facts: Map<string, Fact> }) {
  const style = EVIDENCE_STYLE[h.evidence_status];
  const confidence = h.confidence === null ? null : Number(h.confidence);
  const traceId = h.evidence_trace_ids[0] ?? null;
  return (
    <article className={cx("border px-3 py-2 text-xs", style.box)} aria-label={`Hypothesis ${CATEGORY_LABEL[h.category] ?? h.category}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">{CATEGORY_LABEL[h.category] ?? h.category}</span>
        <span className={cx("rounded border px-1.5 text-2xs font-medium", style.badge)} title={style.help}>{style.label}</span>
        <span className="rounded border border-ink-200 px-1.5 text-2xs text-ink-500" title="Only an experiment can validate a cause">
          {h.validation_status === "validated" ? "Validated" : "Unvalidated hypothesis"}
        </span>
        <span className="ml-auto font-mono tabular-nums" title="Provider confidence, capped by the evidence status">
          {confidence === null ? "confidence n/a" : `confidence ${confidence.toFixed(2)}`}
        </span>
      </div>
      <p className="mt-1.5 text-sm text-ink-900">{h.explanation}</p>
      <dl className="mt-2 grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-2 gap-y-1">
        {h.supporting_facts.length > 0 && <><dt className="text-ink-500">Supported by</dt><dd><FactChips ids={h.supporting_facts} facts={facts} tone="border-emerald-200 text-emerald-800" /></dd></>}
        {h.contradicting_facts.length > 0 && <><dt className="text-ink-500">Contradicted by</dt><dd><FactChips ids={h.contradicting_facts} facts={facts} tone="border-red-200 text-red-800" /></dd></>}
        {h.evidence_span_ids.length > 0 && (
          <><dt className="text-ink-500">Evidence spans</dt>
            <dd className="flex flex-wrap gap-x-2">
              {h.evidence_span_ids.map((s) => <Link key={s} to={spanLink(projectId, traceId, s)!} className="font-mono underline">{s.slice(0, 8)}</Link>)}
            </dd></>
        )}
        {h.uncertainty && <><dt className="text-ink-500">Uncertainty</dt><dd>{h.uncertainty}</dd></>}
        {h.missing_evidence.length > 0 && <><dt className="text-ink-500">Missing</dt><dd>{h.missing_evidence.join(" ")}</dd></>}
        {h.recommended_experiment && <><dt className="text-ink-500">Recommended experiment</dt><dd>{h.recommended_experiment} <span className="text-ink-500">(a recommendation, not evidence)</span></dd></>}
      </dl>
      {h.validation_status === "unvalidated" && h.category !== "insufficient_evidence" && (
        <Link className="mt-2 inline-block text-xs underline" to={`/projects/${projectId}/experiments/new?source_run=${runId}&hypothesis=${h.id}`}>Test with an experiment →</Link>
      )}
    </article>
  );
}
