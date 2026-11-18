import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, GitCompare } from "lucide-react";

import { api, ApiError } from "../api/client";
import type { CaseChange, EvaluationRun, Experiment, ExperimentCase, ExperimentStatus, PairedMeasurement } from "../api/types";
import { useApi } from "../api/useApi";
import { CaseStateBadge, RunStatusBadge, percent } from "../components/evaluation";
import { Button, Card, EmptyState, ErrorState, Field, SectionTitle, Spinner, TextInput, cx } from "../components/ui";
import { formatCost, formatDuration, formatNumber, formatRelative, formatTimestamp } from "../lib/format";
import { CATEGORY_LABEL } from "./DiagnosisPages";

const ACTIVE = new Set<ExperimentStatus>(["pending", "running"]);

function ExperimentStatusBadge({ status }: { status: ExperimentStatus }) {
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

const CHANGE_STYLE: Record<CaseChange, { label: string; style: string }> = {
  fixed: { label: "Fixed", style: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400 font-semibold" },
  regressed: { label: "Regressed", style: "border-red-500/30 bg-red-500/10 text-red-400 font-semibold" },
  unchanged_pass: { label: "Still passing", style: "border-border bg-surface-raised text-slate-300" },
  unchanged_fail: { label: "Still failing", style: "border-amber-500/30 bg-amber-500/10 text-amber-300" },
  incomplete: { label: "Not comparable", style: "border-border bg-surface text-slate-500" },
};

const OUTCOME_LABEL: Record<string, string> = {
  candidate_improves: "Candidate improves",
  candidate_regresses: "Candidate regresses",
  mixed: "Mixed",
  no_change: "No change",
  inconclusive: "Inconclusive",
};

const json = (value: unknown) => JSON.stringify(value);

// --- list ---------------------------------------------------------------------------------------------------

export function ExperimentsPage() {
  const { projectId = "" } = useParams();
  const list = useApi((signal) => api<Experiment[]>(`/projects/${projectId}/experiments`, { signal }), [projectId]);
  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between border-b border-border pb-3">
        <div>
          <div className="flex items-center gap-2">
            <GitCompare className="h-5 w-5 text-slate-500" />
            <h1 className="text-xl font-semibold tracking-tight text-slate-100">Experiments</h1>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Compare a baseline and a candidate configuration on the same dataset. Create one from a completed run.
          </p>
        </div>
      </div>

      {list.error && <ErrorState error={list.error} onRetry={list.reload} />}
      {list.loading && !list.data && (
        <div className="py-12 text-center">
          <Spinner label="Loading experiments" />
        </div>
      )}
      {list.data && list.data.length === 0 && (
        <EmptyState
          title="No experiments yet"
          action={
            <Link className="text-sm underline text-accent" to={`/projects/${projectId}/evaluations`}>
              Go to evaluation runs
            </Link>
          }
        />
      )}
      {list.data && list.data.length > 0 && (
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface shadow-sm">
          {list.data.map((e) => {
            const latest = e.decisions.at(-1);
            return (
              <li key={e.id} className="flex flex-wrap items-center gap-x-4 gap-y-1.5 p-3 text-sm hover:bg-surface-hover/80 transition-colors">
                <ExperimentStatusBadge status={e.status} />
                <Link to={`/projects/${projectId}/experiments/${e.id}`} className="font-medium text-slate-100 hover:text-accent-400 transition-colors">
                  {e.name}
                </Link>
                <span className="font-mono text-xs text-slate-200">
                  {e.config_diff.map((d) => d.path).join(", ")}
                </span>
                {e.comparison.suggested_outcome && (
                  <span className="rounded border border-border bg-surface-raised px-2 py-0.5 text-2xs font-medium text-slate-300">
                    {OUTCOME_LABEL[e.comparison.suggested_outcome]}
                  </span>
                )}
                {latest && (
                  <span
                    className={cx(
                      "text-xs font-semibold rounded px-1.5 py-0.5",
                      latest.decision === "approve"
                        ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                        : "bg-red-500/10 text-red-400 border border-red-500/20",
                    )}
                  >
                    {latest.decision === "approve" ? "Approved" : "Rejected"}
                  </span>
                )}
                <span className="ml-auto text-xs text-slate-500 font-mono" title={formatTimestamp(e.created_at)}>
                  {e.dataset_name} v{e.dataset_version} · {formatRelative(e.created_at)}
                </span>
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
  const source = useApi((signal) => api<EvaluationRun>(`/projects/${projectId}/eval-runs/${sourceRunId}`, { signal }), [
    projectId,
    sourceRunId,
  ]);
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
        body: {
          source_run_id: sourceRunId,
          name,
          description,
          baseline_config: parseObject(baseline, "Baseline configuration"),
          candidate_config: parseObject(candidate, "Candidate configuration"),
          ...(hypothesisId ? { hypothesis_id: hypothesisId } : {}),
        },
      });
      navigate(`/projects/${projectId}/experiments/${experiment.id}`);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (!sourceRunId) return <EmptyState title="Choose a completed evaluation run first" />;
  if (source.error) return <ErrorState error={source.error} />;
  if (!source.data) {
    return (
      <div className="py-12 text-center">
        <Spinner label="Loading run" />
      </div>
    );
  }

  const area =
    "min-h-[8rem] w-full rounded border border-border bg-surface p-2.5 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-accent focus:outline-none";

  return (
    <Card>
      <form onSubmit={submit} className="space-y-4" aria-label="New experiment">
        <h1 className="text-lg font-bold text-slate-100 font-mono">New experiment</h1>
        <p className="text-xs text-slate-400">
          Both arms replay every case of{" "}
          <span className="font-semibold text-slate-200">
            {source.data.dataset_name} v{source.data.dataset_version}
          </span>{" "}
          with the evaluators of{" "}
          <span className="font-semibold text-slate-200">{source.data.name}</span>. Your agent receives each arm's
          configuration; Traceforge does not change your agent.
        </p>
        {source.data.status !== "completed" && (
          <p role="alert" className="text-xs text-red-400 font-medium">
            This run is {source.data.status}; experiments need a completed run.
          </p>
        )}
        {hypothesisId && (
          <p className="rounded border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs text-sky-300">
            This experiment will test a diagnosis hypothesis. Its recommendation is context only; the experiment's results are the evidence.
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name">
            <TextInput value={name} onChange={(e) => setName(e.target.value)} required maxLength={180} />
          </Field>
          <Field label="Description">
            <TextInput value={description} onChange={(e) => setDescription(e.target.value)} maxLength={5000} />
          </Field>
          <Field label="Baseline configuration (JSON object)">
            <textarea className={area} value={baseline} onChange={(e) => setBaseline(e.target.value)} />
          </Field>
          <Field label="Candidate configuration (JSON object)">
            <textarea className={area} value={candidate} onChange={(e) => setCandidate(e.target.value)} />
          </Field>
        </div>
        <Button variant="primary" type="submit">
          Create experiment
        </Button>
        {error && <p role="alert" className="text-xs text-red-400 font-medium">{error}</p>}
      </form>
    </Card>
  );
}

// --- detail ---------------------------------------------------------------------------------------------------

export function ExperimentPage() {
  const { projectId = "", experimentId = "" } = useParams();
  const base = `/projects/${projectId}/experiments/${experimentId}`;
  const experiment = useApi((signal) => api<Experiment>(base, { signal }), [base]);
  const e = experiment.data;
  const cases = useApi(
    (signal) => api<ExperimentCase[]>(`${base}/cases`, { signal }),
    [base, e?.status, e?.baseline.received_cases, e?.candidate.received_cases],
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const active = e ? ACTIVE.has(e.status) : false;

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(experiment.reload, 2500);
    return () => clearInterval(timer);
  }, [active, experiment.reload]);

  if (experiment.error) {
    return experiment.error instanceof ApiError && experiment.error.status === 404 ? (
      <EmptyState title="Experiment not found" />
    ) : (
      <ErrorState error={experiment.error} onRetry={experiment.reload} />
    );
  }
  if (!e) {
    return (
      <div className="py-12 text-center">
        <Spinner label="Loading experiment" />
      </div>
    );
  }

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
    <div className="space-y-4">
      <div>
        <Link
          to={`/projects/${projectId}/experiments`}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-400 hover:text-slate-100 transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          ← Experiments
        </Link>
      </div>

      <Card className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <ExperimentStatusBadge status={e.status} />
          <h1 className="text-lg font-bold text-slate-100 font-mono">{e.name}</h1>
          <span className="text-xs text-slate-400">
            <Link className="hover:underline text-accent-400" to={`/projects/${projectId}/datasets/${e.dataset_id}`}>
              {e.dataset_name} <span className="font-mono">v{e.dataset_version}</span>
            </Link>
            {" · from run "}
            <Link className="hover:underline text-accent-400" to={`/projects/${projectId}/evaluations/${e.source_run_id}`}>
              {e.source_run_name}
            </Link>
          </span>
          <span className="ml-auto flex gap-2">
            {e.status === "running" && armsScored && (
              <Button size="sm" variant="primary" onClick={() => void act("complete")}>
                Compare arms
              </Button>
            )}
            {active && (
              <Button
                size="sm"
                variant="danger"
                onClick={() =>
                  void (window.confirm("Abort this experiment and its arm runs?") && act("abort"))
                }
              >
                Abort
              </Button>
            )}
          </span>
        </div>
        {e.description && <p className="text-xs text-slate-300 font-sans">{e.description}</p>}
        <p className="text-2xs text-slate-500 font-mono">
          Created {formatRelative(e.created_at)}
          {e.deadline_at && e.status === "running" && (
            Date.parse(e.deadline_at) < Date.now() ? (
              <span className="text-red-400 font-semibold">
                {" "}
                · time limit passed at {formatTimestamp(e.deadline_at)}; it will be marked failed shortly
              </span>
            ) : (
              <> · must finish by {formatTimestamp(e.deadline_at)}</>
            )
          )}
        </p>
        {e.status === "failed" && (
          <p role="alert" className="rounded border border-red-500/30 bg-red-950/20 p-2 text-xs text-red-300">
            Failed: {e.failure_reason}
          </p>
        )}
        {actionError && <p role="alert" className="text-xs text-red-400">{actionError}</p>}
      </Card>

      <ConfigSection experiment={e} />
      {e.hypothesis && (
        <HypothesisSection projectId={projectId} experiment={e} onValidate={() => void act("validate-hypothesis")} />
      )}
      <ArmsSection projectId={projectId} experiment={e} />
      {comparison && <ComparisonSection comparison={comparison} />}
      {cases.data && (e.status === "completed" || e.status === "running") && (
        <CasesSection projectId={projectId} cases={cases.data} />
      )}
      <DecisionSection experiment={e} onDecide={(decision, rationale) => act("decisions", { decision, rationale })} />
    </div>
  );
}

function Panel({ title, children, label }: { title: string; children: ReactNode; label?: string }) {
  return (
    <section className="rounded-lg border border-border bg-surface p-4 shadow-sm" aria-label={label ?? title}>
      <SectionTitle>{title}</SectionTitle>
      <div className="pt-1">{children}</div>
    </section>
  );
}

function ConfigSection({ experiment: e }: { experiment: Experiment }) {
  return (
    <Panel title="What differs">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-xs font-medium text-slate-400 border-b border-border pb-1">
            <tr>
              <th className="py-1.5 pr-3 font-semibold text-slate-300">Setting</th>
              <th className="py-1.5 pr-3 font-semibold text-slate-300">Baseline</th>
              <th className="py-1.5 pr-3 font-semibold text-slate-300">Candidate</th>
              <th className="py-1.5 font-semibold text-slate-300">Change</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border font-mono">
            {e.config_diff.map((d) => (
              <tr key={d.path}>
                <td className="py-1.5 pr-3 text-slate-300">{d.path}</td>
                <td className="py-1.5 pr-3 text-slate-400">{d.baseline === null ? "—" : json(d.baseline)}</td>
                <td className="py-1.5 pr-3 font-semibold text-accent-400">{d.candidate === null ? "—" : json(d.candidate)}</td>
                <td className="py-1.5 font-sans text-slate-400">{d.change}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-slate-400 hover:text-slate-200">Full configurations</summary>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <pre className="overflow-auto rounded border border-border bg-sidebar p-2 font-mono text-2xs text-slate-300">
            {JSON.stringify(e.baseline.config, null, 2)}
          </pre>
          <pre className="overflow-auto rounded border border-border bg-sidebar p-2 font-mono text-2xs text-slate-300">
            {JSON.stringify(e.candidate.config, null, 2)}
          </pre>
        </div>
      </details>
    </Panel>
  );
}

function HypothesisSection({
  projectId,
  experiment: e,
  onValidate,
}: {
  projectId: string;
  experiment: Experiment;
  onValidate: () => void;
}) {
  const h = e.hypothesis!;
  return (
    <Panel title="Hypothesis under test">
      <p className="text-sm">
        <span className="font-semibold text-slate-100">{CATEGORY_LABEL[h.category] ?? h.category}</span> on case{" "}
        <span className="font-mono text-slate-200">{h.case_key}</span>
        {" · "}
        <Link className="text-xs text-accent-400 underline font-medium" to={`/projects/${projectId}/diagnoses/${h.diagnosis_id}`}>
          diagnosis
        </Link>
      </p>
      <p className="mt-1 text-xs text-slate-300">{h.explanation}</p>
      <p className="mt-2 border-l-2 border-l-accent-500 pl-2.5 text-xs text-slate-300">
        <span className="font-semibold text-slate-100">Diagnosis recommendation (not evidence): </span>
        {h.recommended_experiment}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        {h.validation_status === "validated" ? (
          <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 font-medium text-emerald-400">
            Validated by {h.validated_by === `experiment:${e.id}` ? "this experiment" : h.validated_by}
          </span>
        ) : (
          <>
            <span className="rounded border border-border bg-surface-raised px-2 py-0.5 text-slate-400">
              Unvalidated
            </span>
            {e.hypothesis_validation?.eligible ? (
              <Button size="sm" variant="secondary" onClick={onValidate}>
                Mark hypothesis validated
              </Button>
            ) : (
              <span className="text-slate-500 text-2xs">{e.hypothesis_validation?.reason}</span>
            )}
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
          <div key={arm} className="rounded-lg border border-border bg-surface-raised p-3 text-xs" aria-label={`${arm} arm`}>
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-slate-100 capitalize">{arm}</span>
              <RunStatusBadge status={e[arm].run_status} />
              <Link className="ml-auto underline text-accent-400 font-mono text-2xs" to={`/projects/${projectId}/evaluations/${e[arm].run_id}`}>
                evaluation run →
              </Link>
            </div>
            <p className="mt-2 font-mono text-2xs text-slate-300">{json(e[arm].config)}</p>
            <p className="mt-1.5 text-2xs text-slate-400 font-mono">
              {e[arm].received_cases}/{e[arm].total_cases} cases executed
            </p>
          </div>
        ))}
      </div>
      {ACTIVE.has(e.status) && (
        <pre className="mt-3 overflow-x-auto rounded border border-border bg-sidebar p-3 font-mono text-xs leading-relaxed text-accent-300">{`import traceforge

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
  const recallDelta =
    b.retrieval.mean_recall !== null && k.retrieval.mean_recall !== null
      ? k.retrieval.mean_recall - b.retrieval.mean_recall
      : null;
  const rows: [string, string, string, string, string?][] = [
    [
      "Pass rate",
      percent(b.pass_rate),
      percent(k.pass_rate),
      c.pass_rate_delta === null ? "—" : `${c.pass_rate_delta > 0 ? "+" : ""}${(c.pass_rate_delta * 100).toFixed(1)} pts`,
    ],
    ["Passed / failed", `${b.passed} / ${b.failed}`, `${k.passed} / ${k.failed}`, ""],
    [
      "Retrieval recall (mean)",
      b.retrieval.mean_recall === null ? "not checked" : b.retrieval.mean_recall.toFixed(2),
      k.retrieval.mean_recall === null ? "not checked" : k.retrieval.mean_recall.toFixed(2),
      recallDelta === null ? "—" : `${recallDelta > 0 ? "+" : ""}${recallDelta.toFixed(2)}`,
    ],
    ["Latency (mean)", latency.b, latency.c, latency.d, `${c.measurements.latency_ms.paired_cases} paired case(s)`],
    ["Tokens (mean)", tokens.b, tokens.c, tokens.d, `${c.measurements.tokens.paired_cases} paired case(s)`],
    ["Cost (mean)", cost.b, cost.c, cost.d, `${c.measurements.cost_usd.paired_cases} paired case(s)`],
  ];
  const evaluators = Array.from(new Set([...Object.keys(b.evaluators), ...Object.keys(k.evaluators)]));
  return (
    <Panel title="Comparison" label="Comparison">
      <p className="mb-3 text-sm">
        <span className="font-semibold text-slate-100">{OUTCOME_LABEL[c.suggested_outcome]}</span>{" "}
        <span className="text-slate-400">— {c.suggested_outcome_reason}</span>
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="overflow-x-auto rounded border border-border bg-surface p-2">
          <table className="w-full text-xs" aria-label="Metrics">
            <thead className="text-left text-xs font-medium text-slate-400 border-b border-border">
              <tr>
                <th className="py-1.5 pr-3 font-semibold text-slate-300">Metric</th>
                <th className="py-1.5 pr-3 text-right font-semibold text-slate-300">Baseline</th>
                <th className="py-1.5 pr-3 text-right font-semibold text-slate-300">Candidate</th>
                <th className="py-1.5 text-right font-semibold text-slate-300">Δ</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map(([label, bv, cv, dv, note]) => (
                <tr key={label}>
                  <td className="py-1.5 pr-3 text-slate-200">
                    {label}
                    {note && <span className="block text-2xs text-slate-500 font-mono">{note}</span>}
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono tabular-nums text-slate-300">{bv}</td>
                  <td className="py-1.5 pr-3 text-right font-mono tabular-nums text-slate-100 font-semibold">{cv}</td>
                  <td className="py-1.5 text-right font-mono tabular-nums text-slate-200 font-medium">{dv}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="space-y-3 text-xs">
          <div className="overflow-x-auto rounded border border-border bg-surface p-2">
            <table className="w-full" aria-label="Evaluators">
              <thead className="text-left text-xs font-medium text-slate-400 border-b border-border">
                <tr>
                  <th className="py-1.5 pr-3 font-semibold text-slate-300">Evaluator</th>
                  <th className="py-1.5 pr-3 text-right font-semibold text-slate-300">Baseline</th>
                  <th className="py-1.5 text-right font-semibold text-slate-300">Candidate</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border font-mono">
                {evaluators.map((name) => (
                  <tr key={name}>
                    <td className="py-1.5 pr-3 text-slate-200">{name}</td>
                    <td className="py-1.5 pr-3 text-right text-slate-400">{percent(b.evaluators[name]?.pass_rate)}</td>
                    <td className="py-1.5 text-right text-slate-100 font-semibold">{percent(k.evaluators[name]?.pass_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>
            <span className="font-semibold text-emerald-400">Fixed:</span>{" "}
            <span className="font-mono text-slate-300">{c.cases.fixed.join(", ") || "none"}</span>
          </p>
          <p>
            <span className="font-semibold text-red-400">Regressed:</span>{" "}
            <span className="font-mono text-slate-300">{c.cases.regressed.join(", ") || "none"}</span>
          </p>
          {c.cases.unchanged_fail.length > 0 && (
            <p>
              <span className="font-semibold text-amber-400">Still failing:</span>{" "}
              <span className="font-mono text-slate-300">{c.cases.unchanged_fail.join(", ")}</span>
            </p>
          )}
        </div>
      </div>
    </Panel>
  );
}

function CasesSection({ projectId, cases }: { projectId: string; cases: ExperimentCase[] }) {
  const trace = (id: string | null, label: string) =>
    id ? (
      <Link to={`/projects/${projectId}/traces/${id}`} className="underline text-accent-400 font-mono text-2xs">
        {label}
      </Link>
    ) : (
      <span className="text-slate-500 font-mono text-2xs">no trace</span>
    );
  return (
    <Panel title="Per case">
      <div className="overflow-x-auto rounded border border-border bg-surface">
        <table className="w-full text-xs" aria-label="Per-case comparison">
          <thead className="text-left text-xs font-medium text-slate-400 bg-surface-raised border-b border-border">
            <tr>
              <th className="py-2 px-3 font-semibold text-slate-300">Case</th>
              <th className="py-2 px-3 font-semibold text-slate-300">Baseline</th>
              <th className="py-2 px-3 font-semibold text-slate-300">Candidate</th>
              <th className="py-2 px-3 font-semibold text-slate-300">Change</th>
              <th className="py-2 px-3 font-semibold text-slate-300">Evaluators</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {cases.map((c) => (
              <tr key={c.case_key} className="align-top hover:bg-surface-hover/80 transition-colors">
                <td className="py-2 px-3 font-mono text-slate-200 font-medium">{c.case_key}</td>
                <td className="py-2 px-3">
                  <CaseStateBadge state={c.baseline.state} />
                  <span className="ml-2">{trace(c.baseline.trace_id, "baseline trace")}</span>
                </td>
                <td className="py-2 px-3">
                  <CaseStateBadge state={c.candidate.state} />
                  <span className="ml-2">{trace(c.candidate.trace_id, "candidate trace")}</span>
                </td>
                <td className="py-2 px-3">
                  <span className={cx("rounded border px-1.5 py-0.5 text-2xs font-mono", CHANGE_STYLE[c.change].style)}>
                    {CHANGE_STYLE[c.change].label}
                  </span>
                </td>
                <td className="py-2 px-3 font-mono text-2xs text-slate-400">
                  {c.evaluator_changes.map((x) => `${x.evaluator} ${x.change}`).join(", ") || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function DecisionSection({
  experiment: e,
  onDecide,
}: {
  experiment: Experiment;
  onDecide: (decision: "approve" | "reject", rationale: string) => Promise<void>;
}) {
  const [rationale, setRationale] = useState("");
  return (
    <Panel title="Promotion decision" label="Promotion decision">
      <p className="mb-3 text-xs text-slate-400">
        A decision records your judgement. Approving does not change any agent, configuration or deployment: apply the candidate configuration yourself.
      </p>
      {e.decisions.length > 0 && (
        <ol className="mb-4 space-y-1.5 text-xs rounded border border-border bg-surface p-3" aria-label="Decision history">
          {e.decisions.map((d) => (
            <li key={d.id} className="flex items-center gap-2">
              <span
                className={cx(
                  "font-medium rounded px-1.5 py-0.5 text-2xs",
                  d.decision === "approve"
                    ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                    : "bg-red-500/10 text-red-400 border border-red-500/20",
                )}
              >
                {d.decision === "approve" ? "Approved" : "Rejected"}
              </span>{" "}
              <span className="text-slate-400 font-mono text-2xs">
                by {d.decided_by ?? "a deleted user"} {formatRelative(d.decided_at)}:
              </span>
              <span className="text-slate-200">{d.rationale}</span>
            </li>
          ))}
        </ol>
      )}
      {e.status === "completed" ? (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(ev) => {
            ev.preventDefault();
          }}
        >
          <Field label="Rationale (required)">
            <TextInput
              className="w-96"
              value={rationale}
              onChange={(ev) => setRationale(ev.target.value)}
              placeholder="Engineering reason for this decision..."
              maxLength={2000}
            />
          </Field>
          <Button
            variant="primary"
            type="button"
            disabled={rationale.trim().length < 3}
            onClick={() => void onDecide("approve", rationale).then(() => setRationale(""))}
          >
            Approve promotion
          </Button>
          <Button
            variant="danger"
            type="button"
            disabled={rationale.trim().length < 3}
            onClick={() => void onDecide("reject", rationale).then(() => setRationale(""))}
          >
            Reject
          </Button>
        </form>
      ) : (
        <p className="text-xs text-slate-500">Decisions can be recorded once the experiment has completed.</p>
      )}
    </Panel>
  );
}
