import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { CaseChange, EvaluationRun, Experiment, ExperimentCase, ExperimentStatus, PairedMeasurement } from "../api/types";
import { useApi } from "../api/useApi";
import { CaseStateBadge, RunStatusBadge, percent } from "../components/evaluation";
import { Button, EmptyState, ErrorState, Field, SectionTitle, Spinner, TextInput, cx } from "../components/ui";
import { formatCost, formatDuration, formatNumber, formatRelative, formatTimestamp } from "../lib/format";
import { CATEGORY_LABEL } from "./DiagnosisPages";

const ACTIVE = new Set<ExperimentStatus>(["pending", "running"]);

function ExperimentStatusBadge({ status }: { status: ExperimentStatus }) {
  const style = { pending: "border-ink-200 text-ink-700", running: "border-sky-300 bg-sky-50 text-sky-800",
    completed: "border-emerald-300 bg-emerald-50 text-emerald-800", failed: "border-red-300 bg-red-50 text-red-800" }[status];
  return <span className={cx("inline-flex rounded border px-1.5 font-mono text-2xs uppercase tracking-wide", style)}>{status}</span>;
}

const CHANGE_STYLE: Record<CaseChange, { label: string; style: string }> = {
  fixed: { label: "Fixed", style: "border-emerald-300 bg-emerald-50 text-emerald-800" },
  regressed: { label: "Regressed", style: "border-red-300 bg-red-50 text-red-800" },
  unchanged_pass: { label: "Still passing", style: "border-ink-200 text-ink-700" },
  unchanged_fail: { label: "Still failing", style: "border-amber-300 bg-amber-50 text-amber-900" },
  incomplete: { label: "Not comparable", style: "border-ink-200 text-ink-500" },
};

const OUTCOME_LABEL: Record<string, string> = {
  candidate_improves: "Candidate improves", candidate_regresses: "Candidate regresses", mixed: "Mixed",
  no_change: "No change", inconclusive: "Inconclusive",
};

const json = (value: unknown) => JSON.stringify(value);

// --- list ---------------------------------------------------------------------------------------------------

export function ExperimentsPage() {
  const { projectId = "" } = useParams();
  const list = useApi((signal) => api<Experiment[]>(`/projects/${projectId}/experiments`, { signal }), [projectId]);
  return (
    <div className="mx-auto max-w-6xl">
      <h1 className="text-lg font-semibold">Experiments</h1>
      <p className="mb-4 text-sm text-ink-500">Baseline versus candidate configurations replayed on the same dataset version. Create one from a completed evaluation run.</p>
      {list.error && <ErrorState error={list.error} onRetry={list.reload} />}
      {list.loading && !list.data && <Spinner label="Loading experiments" />}
      {list.data && list.data.length === 0 && (
        <EmptyState title="No experiments yet" action={<Link className="text-sm underline" to={`/projects/${projectId}/evaluations`}>Go to evaluation runs</Link>} />
      )}
      {list.data && list.data.length > 0 && (
        <ul className="divide-y divide-ink-100 border border-ink-200 bg-white">
          {list.data.map((e) => {
            const latest = e.decisions.at(-1);
            return (
              <li key={e.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-sm">
                <ExperimentStatusBadge status={e.status} />
                <Link to={`/projects/${projectId}/experiments/${e.id}`} className="font-medium hover:underline">{e.name}</Link>
                <span className="font-mono text-xs text-ink-500">{e.config_diff.map((d) => d.path).join(", ")}</span>
                {e.comparison.suggested_outcome && <span className="text-xs">{OUTCOME_LABEL[e.comparison.suggested_outcome]}</span>}
                {latest && <span className={cx("text-xs font-medium", latest.decision === "approve" ? "text-emerald-700" : "text-red-700")}>{latest.decision === "approve" ? "Approved" : "Rejected"}</span>}
                <span className="ml-auto text-xs text-ink-500" title={formatTimestamp(e.created_at)}>{e.dataset_name} v{e.dataset_version} · {formatRelative(e.created_at)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// --- create ---------------------------------------------------------------------------------------------------

function parseObject(text: string, label: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`${label} is not valid JSON.`);
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(`${label} must be a JSON object.`);
  return value as Record<string, unknown>;
}

export function NewExperimentPage() {
  const { projectId = "" } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const sourceRunId = params.get("source_run") ?? "";
  const hypothesisId = params.get("hypothesis");
  const source = useApi((signal) => api<EvaluationRun>(`/projects/${projectId}/eval-runs/${sourceRunId}`, { signal }), [projectId, sourceRunId]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [baseline, setBaseline] = useState('{\n  "retriever": "keyword"\n}');
  const [candidate, setCandidate] = useState('{\n  "retriever": "stemmed"\n}');
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const experiment = await api<Experiment>(`/projects/${projectId}/experiments`, {
        method: "POST",
        body: { source_run_id: sourceRunId, name, description, baseline_config: parseObject(baseline, "Baseline configuration"),
                candidate_config: parseObject(candidate, "Candidate configuration"), ...(hypothesisId ? { hypothesis_id: hypothesisId } : {}) },
      });
      navigate(`/projects/${projectId}/experiments/${experiment.id}`);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (!sourceRunId) return <EmptyState title="Choose a completed evaluation run first" />;
  if (source.error) return <ErrorState error={source.error} />;
  if (!source.data) return <Spinner label="Loading run" />;
  const area = "min-h-[8rem] w-full rounded border border-ink-200 p-2 font-mono text-xs focus:border-ink-700 focus:outline-none";
  return (
    <form onSubmit={submit} className="mx-auto max-w-4xl space-y-4" aria-label="New experiment">
      <h1 className="text-lg font-semibold">New experiment</h1>
      <p className="text-sm text-ink-500">
        Both arms replay every case of <span className="font-medium text-ink-900">{source.data.dataset_name} v{source.data.dataset_version}</span> with the
        evaluators of <span className="font-medium text-ink-900">{source.data.name}</span>. Your agent receives each arm's configuration; Traceforge does not change your agent.
      </p>
      {source.data.status !== "completed" && <p role="alert" className="text-sm text-red-700">This run is {source.data.status}; experiments need a completed run.</p>}
      {hypothesisId && <p className="border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900">This experiment will test a diagnosis hypothesis. Its recommendation is context only; the experiment's results are the evidence.</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><TextInput value={name} onChange={(e) => setName(e.target.value)} required maxLength={180} /></Field>
        <Field label="Description"><TextInput value={description} onChange={(e) => setDescription(e.target.value)} maxLength={5000} /></Field>
        <Field label="Baseline configuration (JSON object)"><textarea className={area} value={baseline} onChange={(e) => setBaseline(e.target.value)} /></Field>
        <Field label="Candidate configuration (JSON object)"><textarea className={area} value={candidate} onChange={(e) => setCandidate(e.target.value)} /></Field>
      </div>
      <Button variant="primary" type="submit">Create experiment</Button>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </form>
  );
}

// --- detail ---------------------------------------------------------------------------------------------------

export function ExperimentPage() {
  const { projectId = "", experimentId = "" } = useParams();
  const base = `/projects/${projectId}/experiments/${experimentId}`;
  const experiment = useApi((signal) => api<Experiment>(base, { signal }), [base]);
  const e = experiment.data;
  const cases = useApi((signal) => api<ExperimentCase[]>(`${base}/cases`, { signal }), [base, e?.status, e?.baseline.received_cases, e?.candidate.received_cases]);
  const [actionError, setActionError] = useState<string | null>(null);
  const active = e ? ACTIVE.has(e.status) : false;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(experiment.reload, 2500);
    return () => clearInterval(timer);
  }, [active, experiment.reload]);

  if (experiment.error) {
    return experiment.error instanceof ApiError && experiment.error.status === 404
      ? <EmptyState title="Experiment not found" />
      : <ErrorState error={experiment.error} onRetry={experiment.reload} />;
  }
  if (!e) return <Spinner label="Loading experiment" />;

  async function act(path: string, body?: unknown) {
    setActionError(null);
    try {
      await api(`${base}/${path}`, { method: "POST", body });
      experiment.reload();
    } catch (err) {
      setActionError((err as Error).message);
    }
  }
  const armsScored = [e.baseline, e.candidate].every((a) => !["pending", "running", "scoring"].includes(a.run_status));
  const comparison = e.status === "completed" ? (e.comparison as Required<Experiment["comparison"]>) : null;

  return (
    <div className="mx-auto max-w-6xl space-y-3">
      <Link to={`/projects/${projectId}/experiments`} className="text-sm text-ink-500 hover:text-ink-900">← Experiments</Link>
      <section className="border border-ink-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <ExperimentStatusBadge status={e.status} />
          <h1 className="text-lg font-semibold">{e.name}</h1>
          <span className="text-sm text-ink-500">
            <Link className="hover:underline" to={`/projects/${projectId}/datasets/${e.dataset_id}`}>{e.dataset_name} <span className="font-mono">v{e.dataset_version}</span></Link>
            {" · from run "}<Link className="hover:underline" to={`/projects/${projectId}/evaluations/${e.source_run_id}`}>{e.source_run_name}</Link>
          </span>
          <span className="ml-auto flex gap-2">
            {e.status === "running" && armsScored && <Button onClick={() => void act("complete")}>Compare arms</Button>}
            {active && <Button variant="danger" onClick={() => void (window.confirm("Abort this experiment and its arm runs?") && act("abort"))}>Abort</Button>}
          </span>
        </div>
        {e.description && <p className="mt-1 text-sm">{e.description}</p>}
        <p className="mt-1 text-xs text-ink-500">Created {formatRelative(e.created_at)}{e.deadline_at && e.status === "running" && (Date.parse(e.deadline_at) < Date.now()
          ? <span className="text-red-700"> · time limit passed at {formatTimestamp(e.deadline_at)}; it will be marked failed shortly</span>
          : <> · must finish by {formatTimestamp(e.deadline_at)}</>)}</p>
        {e.status === "failed" && <p role="alert" className="mt-2 border border-red-200 bg-red-50 px-2.5 py-1.5 text-sm text-red-800">Failed: {e.failure_reason}</p>}
        {actionError && <p role="alert" className="mt-2 text-sm text-red-700">{actionError}</p>}
      </section>

      <ConfigSection experiment={e} />
      {e.hypothesis && <HypothesisSection projectId={projectId} experiment={e} onValidate={() => void act("validate-hypothesis")} />}
      <ArmsSection projectId={projectId} experiment={e} />
      {comparison && <ComparisonSection comparison={comparison} />}
      {cases.data && (e.status === "completed" || e.status === "running") && <CasesSection projectId={projectId} cases={cases.data} />}
      <DecisionSection experiment={e} onDecide={(decision, rationale) => act("decisions", { decision, rationale })} />
    </div>
  );
}

function Panel({ title, children, label }: { title: string; children: ReactNode; label?: string }) {
  return (
    <section className="border border-ink-200 bg-white px-4 py-3" aria-label={label ?? title}>
      <SectionTitle>{title}</SectionTitle>
      {children}
    </section>
  );
}

function ConfigSection({ experiment: e }: { experiment: Experiment }) {
  return (
    <Panel title="What differs">
      <table className="w-full text-xs">
        <thead className="text-left text-2xs uppercase tracking-wider text-ink-500">
          <tr><th className="py-1 pr-3 font-semibold">Setting</th><th className="py-1 pr-3 font-semibold">Baseline</th><th className="py-1 pr-3 font-semibold">Candidate</th><th className="py-1 font-semibold">Change</th></tr>
        </thead>
        <tbody className="divide-y divide-ink-100 font-mono">
          {e.config_diff.map((d) => (
            <tr key={d.path}><td className="py-1 pr-3">{d.path}</td><td className="py-1 pr-3">{d.baseline === null ? "—" : json(d.baseline)}</td>
              <td className="py-1 pr-3 font-semibold">{d.candidate === null ? "—" : json(d.candidate)}</td><td className="py-1 font-sans text-ink-500">{d.change}</td></tr>
          ))}
        </tbody>
      </table>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-ink-500">Full configurations</summary>
        <div className="mt-1 grid gap-2 sm:grid-cols-2">
          <pre className="overflow-auto bg-ink-50 p-2 font-mono">{JSON.stringify(e.baseline.config, null, 2)}</pre>
          <pre className="overflow-auto bg-ink-50 p-2 font-mono">{JSON.stringify(e.candidate.config, null, 2)}</pre>
        </div>
      </details>
    </Panel>
  );
}

function HypothesisSection({ projectId, experiment: e, onValidate }: { projectId: string; experiment: Experiment; onValidate: () => void }) {
  const h = e.hypothesis!;
  return (
    <Panel title="Hypothesis under test">
      <p className="text-sm">
        <span className="font-semibold">{CATEGORY_LABEL[h.category] ?? h.category}</span> on case <span className="font-mono">{h.case_key}</span>
        {" · "}<Link className="text-xs underline" to={`/projects/${projectId}/diagnoses/${h.diagnosis_id}`}>diagnosis</Link>
      </p>
      <p className="mt-1 text-xs text-ink-700">{h.explanation}</p>
      <p className="mt-2 border-l-4 border-l-sky-700 pl-2 text-xs"><span className="font-semibold">Diagnosis recommendation (not evidence): </span>{h.recommended_experiment}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        {h.validation_status === "validated" ? (
          <span className="rounded border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-800">Validated by {h.validated_by === `experiment:${e.id}` ? "this experiment" : h.validated_by}</span>
        ) : (
          <>
            <span className="rounded border border-ink-200 px-1.5 py-0.5 text-ink-700">Unvalidated</span>
            {e.hypothesis_validation?.eligible ? <Button className="h-7 text-xs" onClick={onValidate}>Mark hypothesis validated</Button> : <span className="text-ink-500">{e.hypothesis_validation?.reason}</span>}
          </>
        )}
      </div>
    </Panel>
  );
}

function ArmsSection({ projectId, experiment: e }: { projectId: string; experiment: Experiment }) {
  return (
    <Panel title="Arms">
      <div className="grid gap-3 sm:grid-cols-2">
        {(["baseline", "candidate"] as const).map((arm) => (
          <div key={arm} className="border border-ink-100 px-3 py-2 text-xs" aria-label={`${arm} arm`}>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold capitalize">{arm}</span>
              <RunStatusBadge status={e[arm].run_status} />
              <Link className="ml-auto underline" to={`/projects/${projectId}/evaluations/${e[arm].run_id}`}>evaluation run →</Link>
            </div>
            <p className="mt-1 font-mono">{json(e[arm].config)}</p>
            <p className="mt-1 text-ink-500">{e[arm].received_cases}/{e[arm].total_cases} cases executed</p>
          </div>
        ))}
      </div>
      {ACTIVE.has(e.status) && (
        <pre className="mt-3 overflow-x-auto bg-ink-900 p-3 font-mono text-xs leading-relaxed text-ink-100">{`import traceforge

traceforge.init(api_key="tf_…")
# task(case_input, config) runs your agent with the given configuration
traceforge.run_experiment("${e.id}", task)`}</pre>
      )}
    </Panel>
  );
}

function measure(m: PairedMeasurement, format: (n: number) => string): { b: string; c: string; d: string } {
  if (!m.available) return { b: "not measured", c: "not measured", d: "—" };
  const sign = (m.delta ?? 0) > 0 ? "+" : "";
  return { b: format(m.baseline_mean!), c: format(m.candidate_mean!), d: `${sign}${format(m.delta!)}` };
}

function ComparisonSection({ comparison: c }: { comparison: Required<Experiment["comparison"]> }) {
  const { baseline: b, candidate: k } = c.arms;
  const latency = measure(c.measurements.latency_ms, formatDuration);
  const tokens = measure(c.measurements.tokens, (n) => formatNumber(Math.round(n)));
  const cost = measure(c.measurements.cost_usd, (n) => formatCost(n));
  const recallDelta = b.retrieval.mean_recall !== null && k.retrieval.mean_recall !== null ? k.retrieval.mean_recall - b.retrieval.mean_recall : null;
  const rows: [string, string, string, string, string?][] = [
    ["Pass rate", percent(b.pass_rate), percent(k.pass_rate), c.pass_rate_delta === null ? "—" : `${c.pass_rate_delta > 0 ? "+" : ""}${(c.pass_rate_delta * 100).toFixed(1)} pts`],
    ["Passed / failed", `${b.passed} / ${b.failed}`, `${k.passed} / ${k.failed}`, ""],
    ["Retrieval recall (mean)", b.retrieval.mean_recall === null ? "not checked" : b.retrieval.mean_recall.toFixed(2), k.retrieval.mean_recall === null ? "not checked" : k.retrieval.mean_recall.toFixed(2), recallDelta === null ? "—" : `${recallDelta > 0 ? "+" : ""}${recallDelta.toFixed(2)}`],
    ["Latency (mean)", latency.b, latency.c, latency.d, `${c.measurements.latency_ms.paired_cases} paired case(s)`],
    ["Tokens (mean)", tokens.b, tokens.c, tokens.d, `${c.measurements.tokens.paired_cases} paired case(s)`],
    ["Cost (mean)", cost.b, cost.c, cost.d, `${c.measurements.cost_usd.paired_cases} paired case(s)`],
  ];
  const evaluators = Array.from(new Set([...Object.keys(b.evaluators), ...Object.keys(k.evaluators)]));
  return (
    <Panel title="Comparison" label="Comparison">
      <p className="mb-2 text-sm">
        <span className="font-semibold">{OUTCOME_LABEL[c.suggested_outcome]}</span>{" "}
        <span className="text-ink-500">— {c.suggested_outcome_reason}</span>
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        <table className="w-full text-xs" aria-label="Metrics">
          <thead className="text-left text-2xs uppercase tracking-wider text-ink-500">
            <tr><th className="py-1 pr-3 font-semibold">Metric</th><th className="py-1 pr-3 text-right font-semibold">Baseline</th><th className="py-1 pr-3 text-right font-semibold">Candidate</th><th className="py-1 text-right font-semibold">Δ</th></tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {rows.map(([label, bv, cv, dv, note]) => (
              <tr key={label}><td className="py-1 pr-3">{label}{note && <span className="block text-2xs text-ink-500">{note}</span>}</td>
                <td className="py-1 pr-3 text-right font-mono tabular-nums">{bv}</td><td className="py-1 pr-3 text-right font-mono tabular-nums">{cv}</td>
                <td className="py-1 text-right font-mono tabular-nums">{dv}</td></tr>
            ))}
          </tbody>
        </table>
        <div className="space-y-3 text-xs">
          <table className="w-full" aria-label="Evaluators">
            <thead className="text-left text-2xs uppercase tracking-wider text-ink-500"><tr><th className="py-1 pr-3 font-semibold">Evaluator</th><th className="py-1 pr-3 text-right font-semibold">Baseline</th><th className="py-1 text-right font-semibold">Candidate</th></tr></thead>
            <tbody className="divide-y divide-ink-100 font-mono">
              {evaluators.map((name) => (
                <tr key={name}><td className="py-1 pr-3">{name}</td><td className="py-1 pr-3 text-right">{percent(b.evaluators[name]?.pass_rate)}</td><td className="py-1 text-right">{percent(k.evaluators[name]?.pass_rate)}</td></tr>
              ))}
            </tbody>
          </table>
          <p><span className="font-semibold text-emerald-800">Fixed:</span> {c.cases.fixed.join(", ") || "none"}</p>
          <p><span className="font-semibold text-red-800">Regressed:</span> {c.cases.regressed.join(", ") || "none"}</p>
          {c.cases.unchanged_fail.length > 0 && <p><span className="font-semibold text-amber-900">Still failing:</span> {c.cases.unchanged_fail.join(", ")}</p>}
        </div>
      </div>
    </Panel>
  );
}

function CasesSection({ projectId, cases }: { projectId: string; cases: ExperimentCase[] }) {
  const trace = (id: string | null, label: string) => id ? <Link to={`/projects/${projectId}/traces/${id}`} className="underline">{label}</Link> : <span className="text-ink-500">no trace</span>;
  return (
    <Panel title="Per case">
      <div className="overflow-x-auto">
        <table className="w-full text-xs" aria-label="Per-case comparison">
          <thead className="text-left text-2xs uppercase tracking-wider text-ink-500">
            <tr><th className="py-1 pr-3 font-semibold">Case</th><th className="py-1 pr-3 font-semibold">Baseline</th><th className="py-1 pr-3 font-semibold">Candidate</th><th className="py-1 pr-3 font-semibold">Change</th><th className="py-1 font-semibold">Evaluators</th></tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {cases.map((c) => (
              <tr key={c.case_key} className="align-top">
                <td className="py-1.5 pr-3 font-mono">{c.case_key}</td>
                <td className="py-1.5 pr-3"><CaseStateBadge state={c.baseline.state} /><span className="ml-2">{trace(c.baseline.trace_id, "baseline trace")}</span></td>
                <td className="py-1.5 pr-3"><CaseStateBadge state={c.candidate.state} /><span className="ml-2">{trace(c.candidate.trace_id, "candidate trace")}</span></td>
                <td className="py-1.5 pr-3"><span className={cx("rounded border px-1.5 text-2xs", CHANGE_STYLE[c.change].style)}>{CHANGE_STYLE[c.change].label}</span></td>
                <td className="py-1.5 font-mono">{c.evaluator_changes.map((x) => `${x.evaluator} ${x.change}`).join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function DecisionSection({ experiment: e, onDecide }: { experiment: Experiment; onDecide: (decision: "approve" | "reject", rationale: string) => Promise<void> }) {
  const [rationale, setRationale] = useState("");
  return (
    <Panel title="Promotion decision" label="Promotion decision">
      <p className="mb-2 text-xs text-ink-500">
        A decision records your judgement. Approving does not change any agent, configuration or deployment: apply the candidate configuration yourself.
      </p>
      {e.decisions.length > 0 && (
        <ol className="mb-3 space-y-1 text-xs" aria-label="Decision history">
          {e.decisions.map((d) => (
            <li key={d.id}>
              <span className={cx("font-semibold", d.decision === "approve" ? "text-emerald-700" : "text-red-700")}>{d.decision === "approve" ? "Approved" : "Rejected"}</span>
              {" by "}{d.decided_by ?? "a deleted user"} {formatRelative(d.decided_at)} — {d.rationale}
            </li>
          ))}
        </ol>
      )}
      {e.status === "completed" ? (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(ev) => { ev.preventDefault(); }}>
          <Field label="Rationale (required)"><TextInput className="w-96" value={rationale} onChange={(ev) => setRationale(ev.target.value)} maxLength={2000} /></Field>
          <Button variant="primary" type="button" disabled={rationale.trim().length < 3} onClick={() => void onDecide("approve", rationale).then(() => setRationale(""))}>Approve promotion</Button>
          <Button variant="danger" type="button" disabled={rationale.trim().length < 3} onClick={() => void onDecide("reject", rationale).then(() => setRationale(""))}>Reject</Button>
        </form>
      ) : (
        <p className="text-xs text-ink-500">Decisions can be recorded once the experiment has completed.</p>
      )}
    </Panel>
  );
}
