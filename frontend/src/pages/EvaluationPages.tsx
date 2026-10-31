import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { CaseResult, CaseState, DiagnosisSummary, EvaluationRun, EvaluatorOutcome } from "../api/types";
import { useApi } from "../api/useApi";
import { CaseStateBadge, OutcomeBar, RunStatusBadge, percent, preview } from "../components/evaluation";
import { JsonView } from "../components/JsonView";
import { Button, EmptyState, ErrorState, SectionTitle, Spinner, cx } from "../components/ui";
import { formatDuration, formatRelative, formatTimestamp } from "../lib/format";
import { RunTable } from "./DatasetPage";
import { CATEGORY_LABEL, DiagnoseButton } from "./DiagnosisPages";

const ACTIVE = new Set(["pending", "running", "scoring"]);

export function EvaluationsPage() {
  const { projectId = "" } = useParams();
  const runs = useApi((signal) => api<EvaluationRun[]>(`/projects/${projectId}/eval-runs`, { signal }), [projectId]);
  return (
    <div className="mx-auto max-w-6xl">
      <h1 className="text-lg font-semibold">Evaluations</h1>
      <p className="mb-4 text-sm text-ink-500">Every run scores one exact dataset version. Create runs from a dataset's Evaluations tab.</p>
      {runs.error && <ErrorState error={runs.error} onRetry={runs.reload} />}
      {runs.loading && !runs.data && <Spinner label="Loading runs" />}
      {runs.data && runs.data.length === 0 && (
        <EmptyState title="No evaluation runs yet" action={<Link className="text-sm underline" to={`/projects/${projectId}/datasets`}>Go to datasets</Link>} />
      )}
      {runs.data && runs.data.length > 0 && <RunTable projectId={projectId} runs={runs.data} showDataset />}
    </div>
  );
}

const FILTERS: [CaseState | "", string][] = [["", "All"], ["failed", "Failed"], ["passed", "Passed"], ["missing", "Missing"], ["not_evaluated", "Not evaluated"]];

export function RunPage() {
  const { projectId = "", runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const filter = (params.get("state") as CaseState | null) ?? "";
  const selectedCase = params.get("case");
  const base = `/projects/${projectId}/eval-runs/${runId}`;
  const run = useApi((signal) => api<EvaluationRun>(base, { signal }), [base]);
  const results = useApi((signal) => api<CaseResult[]>(`${base}/results`, { signal }), [base, run.data?.status, run.data?.received_cases]);
  const [actionError, setActionError] = useState<string | null>(null);

  // While the run is active, poll for progress made by the executor.
  const active = run.data ? ACTIVE.has(run.data.status) : false;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(run.reload, 2500);
    return () => clearInterval(timer);
  }, [active, run.reload]);

  if (run.error) {
    return run.error instanceof ApiError && run.error.status === 404 ? (
      <EmptyState title="Evaluation run not found" action={<Link className="text-sm underline" to={`/projects/${projectId}/evaluations`}>All runs</Link>} />
    ) : (
      <ErrorState error={run.error} onRetry={run.reload} />
    );
  }
  if (!run.data) return <Spinner label="Loading run" />;
  const r = run.data;
  const s = r.summary;

  async function act(action: "finish" | "abort" | "rerun") {
    setActionError(null);
    if (action === "abort" && !window.confirm("Abort this run? It becomes failed and accepts no more results.")) return;
    try {
      const next = await api<EvaluationRun>(`${base}/${action}`, { method: "POST" });
      if (action === "rerun") navigate(`/projects/${projectId}/evaluations/${next.id}`);
      else run.reload();
    } catch (err) {
      setActionError((err as Error).message);
    }
  }

  const select = (caseId: string | null) => {
    const next = new URLSearchParams(params);
    if (caseId) next.set("case", caseId);
    else next.delete("case");
    setParams(next, { replace: true });
  };
  const visible = (results.data ?? []).filter((c) => !filter || c.state === filter);
  const current = results.data?.find((c) => c.case_id === selectedCase) ?? null;

  return (
    <div className="space-y-3">
      <Link to={`/projects/${projectId}/evaluations`} className="text-sm text-ink-500 hover:text-ink-900">← Evaluations</Link>
      <section className="border border-ink-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <RunStatusBadge status={r.status} />
          <h1 className="text-lg font-semibold">{r.name}</h1>
          <Link to={`/projects/${projectId}/datasets/${r.dataset_id}?tab=evaluations`} className="text-sm text-ink-500 hover:underline">
            {r.dataset_name} <span className="font-mono">v{r.dataset_version}</span>
          </Link>
          <span className="ml-auto flex gap-2">
            {(r.status === "pending" || r.status === "running") && <Button onClick={() => void act("finish")}>Finish and score</Button>}
            {ACTIVE.has(r.status) && <Button variant="danger" onClick={() => void act("abort")}>Abort</Button>}
            {(r.status === "completed" || r.status === "incomplete") && (s.failed ?? 0) > 0 && <DiagnoseButton projectId={projectId} runId={r.id} />}
            {r.status === "completed" && <Link className="inline-flex h-8 items-center rounded border border-ink-200 bg-white px-3 text-sm font-medium hover:bg-ink-50" to={`/projects/${projectId}/experiments/new?source_run=${r.id}`}>Create experiment</Link>}
            <Button variant="ghost" onClick={() => void act("rerun")}>Re-run</Button>
          </span>
        </div>
        <p className="mt-0.5 font-mono text-2xs text-ink-500">{r.id}</p>
        {actionError && <p role="alert" className="mt-2 text-sm text-red-700">{actionError}</p>}
        <RunTimeline run={r} />
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Pass rate" value={percent(r.pass_rate)} strong title={r.status === "completed" ? undefined : "Only reported for completed runs"} />
          <Stat label="Passed" value={s.passed ?? "—"} />
          <Stat label="Failed" value={s.failed ?? "—"} danger={(s.failed ?? 0) > 0} />
          <Stat label="Received" value={`${r.received_cases}/${r.total_cases}`} />
          <Stat label="Missing" value={s.missing_cases ?? (ACTIVE.has(r.status) ? "—" : r.total_cases - r.received_cases)} warn={(s.missing_cases ?? 0) > 0} />
          <Stat label="Mean score" value={s.mean_score === null || s.mean_score === undefined ? "—" : s.mean_score.toFixed(3)} />
        </dl>
        {r.status === "incomplete" && (
          <p className="mt-3 border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900">
            Incomplete: {s.missing_cases} of {r.total_cases} cases never received a result, so no pass rate is reported. Re-run to evaluate every case.
          </p>
        )}
        {r.status === "failed" && r.failure_reason && (
          <p className="mt-3 border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-800">Failed: {r.failure_reason}</p>
        )}
      </section>

      {ACTIVE.has(r.status) && r.status !== "scoring" && <ExecuteHint runId={r.id} />}
      <RunDiagnoses projectId={projectId} runId={r.id} />

      {s.evaluators && Object.keys(s.evaluators).length > 0 && (
        <section className="border border-ink-200 bg-white px-4 py-3">
          <SectionTitle>Evaluators</SectionTitle>
          <ul className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(s.evaluators).map(([name, e]) => (
              <li key={name} className="text-xs">
                <div className="mb-1 flex justify-between gap-2">
                  <span className="font-mono">{name}</span>
                  <span className="tabular-nums text-ink-500">
                    {e.passed} pass · {e.failed} fail{e.not_applicable ? ` · ${e.not_applicable} n/a` : ""} · {percent(e.pass_rate)}
                  </span>
                </div>
                <OutcomeBar passed={e.passed} failed={e.failed} other={e.not_applicable} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_32rem]">
        <section className="min-w-0">
          <div className="mb-2 flex flex-wrap gap-1" role="tablist" aria-label="Filter cases">
            {FILTERS.map(([value, label]) => {
              const count = value ? (results.data ?? []).filter((c) => c.state === value).length : results.data?.length ?? 0;
              return (
                <button key={label} role="tab" aria-selected={filter === value} type="button"
                  onClick={() => { const next = new URLSearchParams(params); if (value) next.set("state", value); else next.delete("state"); setParams(next, { replace: true }); }}
                  className={cx("rounded border px-2 py-1 text-xs", filter === value ? "border-ink-900 bg-ink-900 text-white" : "border-ink-200 bg-white hover:bg-ink-50")}>
                  {label} <span className="tabular-nums opacity-70">{count}</span>
                </button>
              );
            })}
          </div>
          {results.error && <ErrorState error={results.error} onRetry={results.reload} />}
          {results.data && visible.length === 0 && <EmptyState title="No cases in this filter" />}
          {visible.length > 0 && (
            <ul className="divide-y divide-ink-100 border border-ink-200 bg-white" aria-label="Case results">
              {visible.map((c) => (
                <li key={c.case_id}>
                  <button type="button" onClick={() => select(c.case_id === selectedCase ? null : c.case_id)}
                    className={cx("grid w-full grid-cols-[6.5rem_minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-left text-sm", c.case_id === selectedCase ? "bg-amber-50/70" : "hover:bg-ink-50")}>
                    <CaseStateBadge state={c.state} />
                    <span className="min-w-0">
                      <span className="block truncate font-mono text-xs">{c.key}</span>
                      <span className="block truncate text-xs text-ink-500">{preview(c.input, 120)}</span>
                    </span>
                    <span className="text-right font-mono text-2xs tabular-nums text-ink-500">
                      {c.score !== null ? Number(c.score).toFixed(2) : ""}
                      {c.trace_id && <span className="ml-2 text-ink-700">trace</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        <aside className="border border-ink-200 bg-white lg:sticky lg:top-16 lg:max-h-[calc(100vh-5rem)] lg:overflow-y-auto">
          {current ? <CaseDetail projectId={projectId} result={current} /> : <p className="px-4 py-6 text-sm text-ink-500">Select a case to see its evaluator results.</p>}
        </aside>
      </div>
    </div>
  );
}

function RunTimeline({ run }: { run: EvaluationRun }) {
  const steps: [string, string | null][] = [
    ["created", run.created_at], ["running", run.started_at], ["scoring", run.scoring_started_at], [run.status === "failed" ? "failed" : run.status === "incomplete" ? "incomplete" : "completed", run.finished_at],
  ];
  return (
    <ol className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-2xs text-ink-500" aria-label="Run progress">
      {steps.map(([label, at]) => (
        <li key={label} className={cx("flex items-center gap-1.5", at && "text-ink-900")}>
          <span className={cx("h-1.5 w-1.5 rounded-full", at ? "bg-ink-900" : "border border-ink-300")} aria-hidden />
          <span className="uppercase tracking-wide">{label}</span>
          {at && <span className="font-mono" title={formatTimestamp(at)}>{formatRelative(at)}</span>}
        </li>
      ))}
    </ol>
  );
}

function Stat({ label, value, strong, danger, warn, title }: { label: string; value: string | number; strong?: boolean; danger?: boolean; warn?: boolean; title?: string }) {
  return (
    <div title={title}>
      <dt className="text-2xs uppercase tracking-wider text-ink-500">{label}</dt>
      <dd className={cx("mt-0.5 font-mono tabular-nums", strong && "text-base font-semibold", danger && "font-semibold text-red-700", warn && "font-semibold text-amber-800")}>{value}</dd>
    </div>
  );
}

function ExecuteHint({ runId }: { runId: string }) {
  return (
    <section className="border border-ink-200 bg-white px-4 py-3 text-sm">
      <SectionTitle>Execute this run with your agent</SectionTitle>
      <p className="text-xs text-ink-500">
        Traceforge does not run your agent. Execute the cases with the Python SDK; each case runs in its own trace and the run is
        scored when it finishes. This page refreshes automatically.
      </p>
      <pre className="mt-2 overflow-x-auto bg-ink-900 p-3 font-mono text-xs leading-relaxed text-ink-100">{`import traceforge

traceforge.init(api_key="tf_…")
traceforge.evaluate("${runId}", lambda case_input: {"answer": my_agent(case_input)})`}</pre>
    </section>
  );
}

function CaseDetail({ projectId, result }: { projectId: string; result: CaseResult }) {
  const traceLink = (traceId: string, spanId?: string) => `/projects/${projectId}/traces/${traceId}${spanId ? `?span=${spanId}` : ""}`;
  return (
    <div aria-label={`Case ${result.key}`}>
      <header className="px-4 pb-3 pt-3.5">
        <CaseStateBadge state={result.state} />
        <h2 className="mt-1 break-words font-mono text-sm font-semibold">{result.key}</h2>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-500">
          {result.score !== null && <span>score <span className="font-mono text-ink-900">{Number(result.score).toFixed(3)}</span></span>}
          {result.latency_ms !== null && <span>latency <span className="font-mono text-ink-900">{formatDuration(result.latency_ms)}</span></span>}
          {result.trace_id ? (
            <Link to={traceLink(result.trace_id)} className="font-medium text-ink-900 underline">Open trace →</Link>
          ) : (
            <span>{result.state === "missing" ? "no result was submitted" : "no linked trace"}</span>
          )}
        </div>
      </header>
      {result.execution_error && (
        <Block title="Execution error">
          <p className="border border-red-200 bg-red-50 p-2 text-xs text-red-900">
            <span className="font-mono font-semibold">{result.execution_error.type}</span> {result.execution_error.message}
          </p>
        </Block>
      )}
      {result.evaluations.length > 0 && (
        <Block title="Evaluator results">
          <ul className="space-y-2">
            {result.evaluations.map((e) => <Outcome key={e.evaluator} outcome={e} traceLink={traceLink} />)}
          </ul>
        </Block>
      )}
      <Block title="Input"><JsonView value={result.input} /></Block>
      <Block title="Expected output"><JsonView value={result.expected_output} emptyLabel="None" /></Block>
      {result.expected_sources.length > 0 && (
        <Block title="Expected sources"><p className="font-mono text-xs">{result.expected_sources.join(", ")}</p></Block>
      )}
      <Block title="Actual output"><JsonView value={result.output} emptyLabel={result.state === "missing" ? "No result was submitted" : "None"} /></Block>
    </div>
  );
}

function Outcome({ outcome, traceLink }: { outcome: EvaluatorOutcome; traceLink: (t: string, s?: string) => string }) {
  const state = !outcome.applicable ? "n/a" : outcome.passed ? "pass" : "fail";
  return (
    <li className={cx("border px-2.5 py-2 text-xs", state === "fail" ? "border-red-200" : "border-ink-200")}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono font-semibold">{outcome.evaluator}</span>
        <span className={cx("font-mono uppercase", state === "pass" && "text-emerald-700", state === "fail" && "text-red-700", state === "n/a" && "text-ink-500")}>
          {state}{outcome.score !== null ? ` · ${outcome.score.toFixed(2)}` : ""}
        </span>
      </div>
      <p className="mt-1 text-ink-700">{outcome.explanation}</p>
      {outcome.applicable && (
        <dl className="mt-1.5 grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-2 gap-y-1">
          <dt className="text-ink-500">Expected</dt>
          <dd className="break-words font-mono">{preview(outcome.expected, 300)}</dd>
          <dt className="text-ink-500">Actual</dt>
          <dd className="break-words font-mono">{preview(outcome.actual, 300)}</dd>
        </dl>
      )}
      {outcome.supporting_refs.some((r) => r.span_id) && (
        <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
          {outcome.supporting_refs.filter((r) => r.span_id).map((ref) => (
            <Link key={ref.span_id} to={traceLink(ref.trace_id, ref.span_id)} className="text-ink-900 underline">
              {ref.reason ?? "span"} {ref.span_id?.slice(0, 8)}
            </Link>
          ))}
        </p>
      )}
    </li>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-ink-100 px-4 py-3">
      <SectionTitle>{title}</SectionTitle>
      {children}
    </section>
  );
}

function RunDiagnoses({ projectId, runId }: { projectId: string; runId: string }) {
  const list = useApi((signal) => api<DiagnosisSummary[]>(`/projects/${projectId}/diagnoses`, { query: { run_id: runId }, signal }), [projectId, runId]);
  if (!list.data || list.data.length === 0) return null;
  return (
    <section className="border border-ink-200 bg-white px-4 py-3">
      <SectionTitle>Diagnoses of this run</SectionTitle>
      <ul className="space-y-1 text-sm">
        {list.data.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center gap-x-3">
            <Link className="underline" to={`/projects/${projectId}/diagnoses/${d.id}`}>{formatRelative(d.created_at)}</Link>
            <span className="font-mono text-2xs uppercase text-ink-500">{d.status}</span>
            <span className="text-xs text-ink-500">{Object.entries(d.category_counts).map(([c, n]) => `${CATEGORY_LABEL[c] ?? c} ×${n}`).join(" · ")}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
