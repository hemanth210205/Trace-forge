import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, FlaskConical } from "lucide-react";

import { api, ApiError } from "../api/client";
import type { CaseResult, CaseState, DiagnosisSummary, EvaluationRun, EvaluatorOutcome } from "../api/types";
import { useApi } from "../api/useApi";
import { CaseStateBadge, OutcomeBar, RunStatusBadge, percent, preview } from "../components/evaluation";
import { JsonView } from "../components/JsonView";
import { Button, Card, EmptyState, ErrorState, SectionTitle, Spinner, cx } from "../components/ui";
import { formatDuration, formatRelative, formatTimestamp } from "../lib/format";
import { RunTable } from "./DatasetPage";
import { CATEGORY_LABEL, DiagnoseButton } from "./DiagnosisPages";

const ACTIVE = new Set(["pending", "running", "scoring"]);

export function EvaluationsPage() {
  const { projectId = "" } = useParams();
  const runs = useApi((signal) => api<EvaluationRun[]>(`/projects/${projectId}/eval-runs`, { signal }), [projectId]);
  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between border-b border-border pb-3">
        <div>
          <div className="flex items-center gap-2">
            <FlaskConical className="h-5 w-5 text-slate-500" />
            <h1 className="text-xl font-semibold tracking-tight text-slate-100">Evaluations</h1>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Every run scores one exact dataset version. Create runs from a dataset's Evaluations tab.
          </p>
        </div>
      </div>

      {runs.error && <ErrorState error={runs.error} onRetry={runs.reload} />}
      {runs.loading && !runs.data && (
        <div className="py-12 text-center">
          <Spinner label="Loading runs" />
        </div>
      )}
      {runs.data && runs.data.length === 0 && (
        <EmptyState
          title="No evaluation runs yet"
          action={
            <Link className="text-sm underline text-accent" to={`/projects/${projectId}/datasets`}>
              Go to datasets
            </Link>
          }
        />
      )}
      {runs.data && runs.data.length > 0 && <RunTable projectId={projectId} runs={runs.data} showDataset />}
    </div>
  );
}

const FILTERS: [CaseState | "", string][] = [
  ["", "All"],
  ["failed", "Failed"],
  ["passed", "Passed"],
  ["missing", "Missing"],
  ["not_evaluated", "Not evaluated"],
];

export function RunPage() {
  const { projectId = "", runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const filter = (params.get("state") as CaseState | null) ?? "";
  const selectedCase = params.get("case");
  const base = `/projects/${projectId}/eval-runs/${runId}`;
  const run = useApi((signal) => api<EvaluationRun>(base, { signal }), [base]);
  const results = useApi((signal) => api<CaseResult[]>(`${base}/results`, { signal }), [
    base,
    run.data?.status,
    run.data?.received_cases,
  ]);
  const [actionError, setActionError] = useState<string | null>(null);

  const active = run.data ? ACTIVE.has(run.data.status) : false;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(run.reload, 2500);
    return () => clearInterval(timer);
  }, [active, run.reload]);

  if (run.error) {
    return run.error instanceof ApiError && run.error.status === 404 ? (
      <EmptyState
        title="Evaluation run not found"
        action={
          <Link className="text-sm underline text-accent" to={`/projects/${projectId}/evaluations`}>
            All runs
          </Link>
        }
      />
    ) : (
      <ErrorState error={run.error} onRetry={run.reload} />
    );
  }
  if (!run.data) {
    return (
      <div className="py-12 text-center">
        <Spinner label="Loading run" />
      </div>
    );
  }
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
    <div className="space-y-4">
      {/* Back link */}
      <div>
        <Link
          to={`/projects/${projectId}/evaluations`}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-400 hover:text-slate-100 transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          ← Evaluations
        </Link>
      </div>

      {/* Run Summary Header Card */}
      <Card className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <RunStatusBadge status={r.status} />
          <h1 className="text-lg font-bold text-slate-100 font-mono">{r.name}</h1>
          <Link
            to={`/projects/${projectId}/datasets/${r.dataset_id}?tab=evaluations`}
            className="text-xs text-slate-400 hover:text-accent-400 hover:underline transition-colors"
          >
            {r.dataset_name} <span className="font-mono text-slate-200">v{r.dataset_version}</span>
          </Link>
          <span className="ml-auto flex items-center gap-2">
            {(r.status === "pending" || r.status === "running") && (
              <Button size="sm" variant="secondary" onClick={() => void act("finish")}>
                Finish and score
              </Button>
            )}
            {ACTIVE.has(r.status) && (
              <Button size="sm" variant="danger" onClick={() => void act("abort")}>
                Abort
              </Button>
            )}
            {(r.status === "completed" || r.status === "incomplete") && (s.failed ?? 0) > 0 && (
              <DiagnoseButton projectId={projectId} runId={r.id} />
            )}
            {r.status === "completed" && (
              <Link
                className="inline-flex h-8 items-center rounded border border-border bg-surface px-3 text-xs font-medium text-slate-200 hover:bg-surface-hover hover:text-white transition-colors"
                to={`/projects/${projectId}/experiments/new?source_run=${r.id}`}
              >
                Create experiment
              </Link>
            )}
            <Button size="sm" variant="ghost" onClick={() => void act("rerun")}>
              Re-run
            </Button>
          </span>
        </div>
        <p className="font-mono text-2xs text-slate-500">{r.id}</p>
        {actionError && <p role="alert" className="text-xs text-red-400">{actionError}</p>}
        <RunTimeline run={r} />
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 pt-3 text-xs sm:grid-cols-3 lg:grid-cols-6 border-t border-border">
          <Stat
            label="Pass rate"
            value={percent(r.pass_rate)}
            strong
            title={r.status === "completed" ? undefined : "Only reported for completed runs"}
          />
          <Stat label="Passed" value={s.passed ?? "—"} />
          <Stat label="Failed" value={s.failed ?? "—"} danger={(s.failed ?? 0) > 0} />
          <Stat label="Received" value={`${r.received_cases}/${r.total_cases}`} />
          <Stat
            label="Missing"
            value={s.missing_cases ?? (ACTIVE.has(r.status) ? "—" : r.total_cases - r.received_cases)}
            warn={(s.missing_cases ?? 0) > 0}
          />
          <Stat
            label="Mean score"
            value={s.mean_score === null || s.mean_score === undefined ? "—" : s.mean_score.toFixed(3)}
          />
        </dl>
        {r.status === "incomplete" && (
          <p className="rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
            Incomplete: {s.missing_cases} of {r.total_cases} cases never received a result, so no pass rate is reported. Re-run to evaluate every case.
          </p>
        )}
        {r.status === "failed" && r.failure_reason && (
          <p className="rounded border border-red-500/30 bg-red-950/20 px-3 py-2 text-xs text-red-300">
            Failed: {r.failure_reason}
          </p>
        )}
      </Card>

      {ACTIVE.has(r.status) && r.status !== "scoring" && <ExecuteHint runId={r.id} />}
      <RunDiagnoses projectId={projectId} runId={r.id} />

      {/* Evaluator Outcomes Grid */}
      {s.evaluators && Object.keys(s.evaluators).length > 0 && (
        <Card>
          <SectionTitle>Evaluators</SectionTitle>
          <ul className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3 pt-1">
            {Object.entries(s.evaluators).map(([name, e]) => (
              <li key={name} className="text-xs">
                <div className="mb-1.5 flex justify-between gap-2">
                  <span className="font-mono text-slate-200">{name}</span>
                  <span className="tabular-nums text-slate-400 font-mono text-2xs">
                    {e.passed} pass · {e.failed} fail{e.not_applicable ? ` · ${e.not_applicable} n/a` : ""} · {percent(e.pass_rate)}
                  </span>
                </div>
                <OutcomeBar passed={e.passed} failed={e.failed} other={e.not_applicable} />
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Cases Breakdown Split View */}
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_32rem]">
        <section className="min-w-0 space-y-3">
          <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Filter cases">
            {FILTERS.map(([value, label]) => {
              const count = value ? (results.data ?? []).filter((c) => c.state === value).length : results.data?.length ?? 0;
              return (
                <button
                  key={label}
                  role="tab"
                  aria-selected={filter === value}
                  type="button"
                  onClick={() => {
                    const next = new URLSearchParams(params);
                    if (value) next.set("state", value);
                    else next.delete("state");
                    setParams(next, { replace: true });
                  }}
                  className={cx(
                    "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
                    filter === value
                      ? "border-accent bg-accent-400 text-[#09090B]"
                      : "border-border bg-surface text-slate-400 hover:bg-surface-hover hover:text-slate-200",
                  )}
                >
                  {label} <span className="tabular-nums font-mono opacity-80 text-2xs">{count}</span>
                </button>
              );
            })}
          </div>

          {results.error && <ErrorState error={results.error} onRetry={results.reload} />}
          {results.data && visible.length === 0 && <EmptyState title="No cases in this filter" />}

          {visible.length > 0 && (
            <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface shadow-sm" aria-label="Case results">
              {visible.map((c) => (
                <li key={c.case_id}>
                  <button
                    type="button"
                    onClick={() => select(c.case_id === selectedCase ? null : c.case_id)}
                    className={cx(
                      "grid w-full grid-cols-[6.5rem_minmax(0,1fr)_auto] items-center gap-3 px-3.5 py-2.5 text-left text-xs transition-colors",
                      c.case_id === selectedCase
                        ? "bg-accent-500/15 border-l-2 border-l-accent-500"
                        : "hover:bg-surface-hover/80",
                    )}
                  >
                    <CaseStateBadge state={c.state} />
                    <span className="min-w-0">
                      <span className="block truncate font-mono font-medium text-slate-200">{c.key}</span>
                      <span className="block truncate text-2xs text-slate-400 mt-0.5">{preview(c.input, 120)}</span>
                    </span>
                    <span className="text-right font-mono text-2xs tabular-nums text-slate-400">
                      {c.score !== null ? Number(c.score).toFixed(2) : ""}
                      {c.trace_id && <span className="ml-2 text-slate-200">trace</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Selected Case Inspector */}
        <aside className="rounded-lg border border-border bg-surface lg:sticky lg:top-18 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto shadow-sm">
          {current ? (
            <CaseDetail projectId={projectId} result={current} />
          ) : (
            <p className="px-4 py-8 text-center text-xs text-slate-500">
              Select a case to inspect its evaluator results, inputs and trace evidence.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}

function RunTimeline({ run }: { run: EvaluationRun }) {
  const steps: [string, string | null][] = [
    ["created", run.created_at],
    ["running", run.started_at],
    ["scoring", run.scoring_started_at],
    [
      run.status === "failed" ? "failed" : run.status === "incomplete" ? "incomplete" : "completed",
      run.finished_at,
    ],
  ];
  return (
    <ol className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-2xs text-slate-400" aria-label="Run progress">
      {steps.map(([label, at]) => (
        <li key={label} className={cx("flex items-center gap-1.5", at && "text-slate-200")}>
          <span className={cx("h-1.5 w-1.5 rounded-full", at ? "bg-accent-400" : "border border-slate-600")} aria-hidden />
          <span className="font-medium">{label}</span>
          {at && <span className="font-mono text-slate-500" title={formatTimestamp(at)}>{formatRelative(at)}</span>}
        </li>
      ))}
    </ol>
  );
}

function Stat({
  label,
  value,
  strong,
  danger,
  warn,
  title,
}: {
  label: string;
  value: string | number;
  strong?: boolean;
  danger?: boolean;
  warn?: boolean;
  title?: string;
}) {
  return (
    <div title={title}>
      <dt className="text-xs font-medium text-slate-500">{label}</dt>
      <dd
        className={cx(
          "mt-0.5 font-mono tabular-nums text-xs",
          strong && "text-base font-bold text-slate-100",
          danger && "font-semibold text-red-400",
          warn && "font-semibold text-amber-400",
          !strong && !danger && !warn && "text-slate-200",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function ExecuteHint({ runId }: { runId: string }) {
  return (
    <Card className="text-sm">
      <SectionTitle>Execute this run with your agent</SectionTitle>
      <p className="text-xs text-slate-400">
        Traceforge does not run your agent. Execute the cases with the Python SDK; each case runs in its own trace and the run is
        scored when it finishes. This page refreshes automatically.
      </p>
      <pre className="mt-2 overflow-x-auto rounded border border-border bg-sidebar p-3 font-mono text-xs leading-relaxed text-accent-300">{`import traceforge

traceforge.init(api_key="tf_…")
traceforge.evaluate("${runId}", lambda case_input: {"answer": my_agent(case_input)})`}</pre>
    </Card>
  );
}

function CaseDetail({ projectId, result }: { projectId: string; result: CaseResult }) {
  const traceLink = (traceId: string, spanId?: string) =>
    `/projects/${projectId}/traces/${traceId}${spanId ? `?span=${spanId}` : ""}`;
  return (
    <div aria-label={`Case ${result.key}`}>
      <header className="px-4 pb-3 pt-3.5 border-b border-border">
        <CaseStateBadge state={result.state} />
        <h2 className="mt-1.5 break-words font-mono text-sm font-semibold text-slate-100">{result.key}</h2>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
          {result.score !== null && (
            <span>
              score <span className="font-mono text-slate-200">{Number(result.score).toFixed(3)}</span>
            </span>
          )}
          {result.latency_ms !== null && (
            <span>
              latency <span className="font-mono text-slate-200">{formatDuration(result.latency_ms)}</span>
            </span>
          )}
          {result.trace_id ? (
            <Link to={traceLink(result.trace_id)} className="font-medium text-accent-400 hover:underline">
              Open trace →
            </Link>
          ) : (
            <span>{result.state === "missing" ? "no result was submitted" : "no linked trace"}</span>
          )}
        </div>
      </header>

      {result.execution_error && (
        <Block title="Execution error">
          <p className="rounded border border-red-500/30 bg-red-950/20 p-2.5 text-xs text-red-200">
            <span className="font-mono font-semibold text-red-300">{result.execution_error.type}</span>{" "}
            {result.execution_error.message}
          </p>
        </Block>
      )}

      {result.evaluations.length > 0 && (
        <Block title="Evaluator results">
          <ul className="space-y-2">
            {result.evaluations.map((e) => (
              <Outcome key={e.evaluator} outcome={e} traceLink={traceLink} />
            ))}
          </ul>
        </Block>
      )}

      <Block title="Input">
        <JsonView value={result.input} />
      </Block>
      <Block title="Expected output">
        <JsonView value={result.expected_output} emptyLabel="None" />
      </Block>
      {result.expected_sources.length > 0 && (
        <Block title="Expected sources">
          <p className="font-mono text-xs text-slate-300">{result.expected_sources.join(", ")}</p>
        </Block>
      )}
      <Block title="Actual output">
        <JsonView
          value={result.output}
          emptyLabel={result.state === "missing" ? "No result was submitted" : "None"}
        />
      </Block>
    </div>
  );
}

function Outcome({
  outcome,
  traceLink,
}: {
  outcome: EvaluatorOutcome;
  traceLink: (t: string, s?: string) => string;
}) {
  const state = !outcome.applicable ? "n/a" : outcome.passed ? "pass" : "fail";
  return (
    <li
      className={cx(
        "rounded-lg border p-2.5 text-xs",
        state === "fail" ? "border-red-500/30 bg-red-950/15" : "border-border bg-surface",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono font-semibold text-slate-200">{outcome.evaluator}</span>
        <span
          className={cx(
            "font-mono uppercase font-semibold text-2xs",
            state === "pass" && "text-emerald-400",
            state === "fail" && "text-red-400",
            state === "n/a" && "text-slate-500",
          )}
        >
          {state}
          {outcome.score !== null ? ` · ${outcome.score.toFixed(2)}` : ""}
        </span>
      </div>
      <p className="mt-1 text-slate-300">{outcome.explanation}</p>
      {outcome.applicable && (
        <dl className="mt-2 grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-2xs">
          <dt className="text-slate-500">Expected</dt>
          <dd className="break-words font-mono text-slate-300">{preview(outcome.expected, 300)}</dd>
          <dt className="text-slate-500">Actual</dt>
          <dd className="break-words font-mono text-slate-300">{preview(outcome.actual, 300)}</dd>
        </dl>
      )}
      {outcome.supporting_refs.some((r) => r.span_id) && (
        <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-2xs">
          {outcome.supporting_refs
            .filter((r) => r.span_id)
            .map((ref) => (
              <Link key={ref.span_id} to={traceLink(ref.trace_id, ref.span_id)} className="text-accent-400 underline font-mono">
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
    <section className="border-t border-border px-4 py-3">
      <SectionTitle>{title}</SectionTitle>
      {children}
    </section>
  );
}

function RunDiagnoses({ projectId, runId }: { projectId: string; runId: string }) {
  const list = useApi(
    (signal) => api<DiagnosisSummary[]>(`/projects/${projectId}/diagnoses`, { query: { run_id: runId }, signal }),
    [projectId, runId],
  );
  if (!list.data || list.data.length === 0) return null;
  return (
    <Card>
      <SectionTitle>Diagnoses of this run</SectionTitle>
      <ul className="space-y-1.5 text-xs pt-1">
        {list.data.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center gap-x-3">
            <Link className="text-accent-400 underline font-medium" to={`/projects/${projectId}/diagnoses/${d.id}`}>
              {formatRelative(d.created_at)}
            </Link>
            <span className="font-mono text-2xs uppercase text-slate-500">{d.status}</span>
            <span className="text-slate-400">
              {Object.entries(d.category_counts)
                .map(([c, n]) => `${CATEGORY_LABEL[c] ?? c} ×${n}`)
                .join(" · ")}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
