import { Link, useParams } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Database,
  FlaskConical,
  GitCompare,
  Key,
  ListTree,
  Zap,
} from "lucide-react";

import { api } from "../api/client";
import type { Dataset, DiagnosisSummary, EvaluationRun, Experiment, TraceSummary } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, Card, EmptyState, MetricCard, Spinner, StatusBadge } from "../components/ui";

export function OverviewPage() {
  const { projectId } = useParams<{ projectId: string }>();

  const tracesApi = useApi(
    (signal) => (projectId ? api<{ items: TraceSummary[] }>(`/projects/${projectId}/traces?limit=50`, { signal }) : Promise.resolve({ items: [] })),
    [projectId],
  );

  const evalRunsApi = useApi(
    (signal) => (projectId ? api<EvaluationRun[]>(`/projects/${projectId}/eval-runs`, { signal }) : Promise.resolve([])),
    [projectId],
  );

  const diagnosesApi = useApi(
    (signal) => (projectId ? api<DiagnosisSummary[]>(`/projects/${projectId}/diagnoses`, { signal }) : Promise.resolve([])),
    [projectId],
  );

  const experimentsApi = useApi(
    (signal) => (projectId ? api<Experiment[]>(`/projects/${projectId}/experiments`, { signal }) : Promise.resolve([])),
    [projectId],
  );

  const datasetsApi = useApi(
    (signal) => (projectId ? api<Dataset[]>(`/projects/${projectId}/datasets`, { signal }) : Promise.resolve([])),
    [projectId],
  );

  const loading = tracesApi.loading || evalRunsApi.loading || diagnosesApi.loading;

  const traces = tracesApi.data?.items ?? [];
  const evalRuns = evalRunsApi.data ?? [];
  const diagnoses = diagnosesApi.data ?? [];
  const experiments = experimentsApi.data ?? [];
  const datasets = datasetsApi.data ?? [];

  // Computed metrics
  const totalTraces = traces.length;
  const errorTraces = traces.filter((t) => t.status === "error" || (t.error_count ?? 0) > 0).length;
  const errorRate = totalTraces > 0 ? ((errorTraces / totalTraces) * 100).toFixed(1) : "0.0";
  const avgLatency =
    totalTraces > 0
      ? Math.round(traces.reduce((acc, t) => acc + (t.duration_ms ?? 0), 0) / totalTraces)
      : 0;

  const totalRuns = evalRuns.length;
  const completedRuns = evalRuns.filter((r) => r.status === "completed");
  const latestRun = completedRuns[0];
  const latestPassRate = latestRun && latestRun.pass_rate !== null
    ? Math.round(latestRun.pass_rate * 100)
    : null;

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-100 flex items-center gap-2">
            <Activity className="h-5 w-5 text-slate-500" />
            Overview
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            What this project's agent has been doing recently.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link to={`/projects/${projectId}/traces`}>
            <Button variant="secondary" size="sm">
              <ListTree className="h-3.5 w-3.5" />
              View traces
            </Button>
          </Link>
          <Link to={`/projects/${projectId}/evaluations`}>
            <Button variant="primary" size="sm">
              <FlaskConical className="h-3.5 w-3.5" />
              Evaluations
            </Button>
          </Link>
        </div>
      </div>

      {loading && !traces.length ? (
        <div className="py-12 text-center">
          <Spinner label="Loading overview" />
        </div>
      ) : (
        <>
          {/* Top KPI Cards */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="Recent traces"
              value={totalTraces}
              subvalue={`Last ${totalTraces} · ${errorTraces} with errors (${errorRate}%)`}
              tone="neutral"
              icon={<ListTree className="h-4 w-4" />}
            />
            <MetricCard
              label="Average duration"
              value={`${avgLatency} ms`}
              subvalue="Per trace, over recent traces"
              tone="neutral"
              icon={<Zap className="h-4 w-4" />}
            />
            <MetricCard
              label="Evaluation runs"
              value={totalRuns}
              subvalue={latestPassRate !== null ? `Latest pass rate ${latestPassRate}%` : "No completed runs"}
              tone={latestPassRate !== null && latestPassRate >= 80 ? "positive" : "neutral"}
              icon={<CheckCircle2 className="h-4 w-4" />}
            />
            <MetricCard
              label="Diagnoses / experiments"
              value={`${diagnoses.length} / ${experiments.length}`}
              subvalue="Investigations and A/B comparisons"
              tone="neutral"
              icon={<GitCompare className="h-4 w-4" />}
            />
          </div>

          {/* Main 2-Column Dashboard Grid */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* Recent Traces Stream */}
            <Card className="flex flex-col">
              <div className="flex items-center justify-between border-b border-border pb-3 mb-3">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-slate-100">Recent traces</h2>
                </div>
                <Link to={`/projects/${projectId}/traces`} className="text-xs text-accent hover:underline flex items-center gap-1 font-medium">
                  View all <ArrowRight className="h-3 w-3" />
                </Link>
              </div>

              {traces.length === 0 ? (
                <EmptyState title="No traces yet">
                  Create an API key and send traces from your agent with the Python SDK.
                </EmptyState>
              ) : (
                <div className="divide-y divide-border overflow-hidden">
                  {traces.slice(0, 6).map((trace) => (
                    <Link
                      key={trace.id}
                      to={`/projects/${projectId}/traces/${trace.id}`}
                      className="group flex items-center justify-between py-2.5 px-2 hover:bg-surface-hover rounded transition-colors text-xs"
                    >
                      <div className="min-w-0 flex-1 pr-3">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-medium text-slate-200 group-hover:text-accent-400 truncate">
                            {trace.name}
                          </span>
                          <StatusBadge status={trace.status} compact />
                        </div>
                        <div className="mt-0.5 flex items-center gap-2 text-2xs text-slate-500 font-mono">
                          <span>{trace.id.slice(0, 8)}</span>
                          <span>·</span>
                          <span>{trace.span_count} spans</span>
                          {trace.error_count > 0 && (
                            <>
                              <span>·</span>
                              <span className="text-red-400">{trace.error_count} {trace.error_count === 1 ? "error" : "errors"}</span>
                            </>
                          )}
                        </div>
                      </div>
                      <div className="text-right text-2xs font-mono text-slate-400">
                        <div>{trace.duration_ms !== null ? `${trace.duration_ms} ms` : "in progress"}</div>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </Card>

            {/* Evaluations & Diagnoses Activity */}
            <Card className="flex flex-col">
              <div className="flex items-center justify-between border-b border-border pb-3 mb-3">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-slate-100">Evaluation runs</h2>
                </div>
                <Link to={`/projects/${projectId}/evaluations`} className="text-xs text-accent hover:underline flex items-center gap-1 font-medium">
                  View all <ArrowRight className="h-3 w-3" />
                </Link>
              </div>

              {evalRuns.length === 0 ? (
                <EmptyState title="No evaluation runs yet">
                  Publish a dataset version, then start a run from the dataset page.
                </EmptyState>
              ) : (
                <div className="divide-y divide-border overflow-hidden">
                  {evalRuns.slice(0, 5).map((run) => {
                    const passPct = run.pass_rate !== null ? Math.round(run.pass_rate * 100) : 0;
                    const passedCases = run.summary?.passed ?? 0;
                    const failedCases = run.summary?.failed ?? 0;
                    return (
                      <Link
                        key={run.id}
                        to={`/projects/${projectId}/evaluations/${run.id}`}
                        className="group flex items-center justify-between py-2.5 px-2 hover:bg-surface-hover rounded transition-colors text-xs"
                      >
                        <div className="min-w-0 flex-1 pr-3">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-slate-200 group-hover:text-accent-400 truncate">
                              {run.name}
                            </span>
                            <span className="text-2xs rounded bg-surface-raised px-1.5 py-0.5 text-slate-400 font-mono">
                              v{run.dataset_version}
                            </span>
                          </div>
                          <div className="mt-0.5 text-2xs text-slate-500 font-mono">
                            {passedCases} / {run.total_cases} cases passed · {failedCases} failed
                          </div>
                        </div>
                        <div className="text-right">
                          <span
                            className={`inline-block px-2 py-0.5 text-2xs font-semibold rounded font-mono ${
                              passPct === 100
                                ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                                : passPct >= 70
                                ? "bg-accent-500/10 text-accent-400 border border-accent-500/20"
                                : "bg-red-500/10 text-red-400 border border-red-500/20"
                            }`}
                          >
                            {passPct}% pass
                          </span>
                        </div>
                      </Link>
                    );
                  })}
                </div>
              )}
            </Card>
          </div>

          {/* Quick Setup & Key Resources */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Card className="flex flex-col justify-between">
              <div>
                <div className="flex items-center gap-2 text-slate-200 font-semibold text-xs mb-1">
                  <Database className="h-4 w-4 text-slate-500" />
                  Datasets ({datasets.length})
                </div>
                <p className="text-xs text-slate-400">
                  Versioned test cases with expected answers and sources.
                </p>
              </div>
              <div className="mt-4">
                <Link to={`/projects/${projectId}/datasets`}>
                  <Button variant="secondary" size="sm" className="w-full">
                    Open datasets
                  </Button>
                </Link>
              </div>
            </Card>

            <Card className="flex flex-col justify-between">
              <div>
                <div className="flex items-center gap-2 text-slate-200 font-semibold text-xs mb-1">
                  <GitCompare className="h-4 w-4 text-slate-500" />
                  Experiments ({experiments.length})
                </div>
                <p className="text-xs text-slate-400">
                  Test a configuration change against the current one, case by case.
                </p>
              </div>
              <div className="mt-4">
                <Link to={`/projects/${projectId}/experiments`}>
                  <Button variant="secondary" size="sm" className="w-full">
                    Open experiments
                  </Button>
                </Link>
              </div>
            </Card>

            <Card className="flex flex-col justify-between">
              <div>
                <div className="flex items-center gap-2 text-slate-200 font-semibold text-xs mb-1">
                  <Key className="h-4 w-4 text-slate-500" />
                  API keys
                </div>
                <p className="text-xs text-slate-400">
                  Keys let the Python SDK send traces to this project.
                </p>
              </div>
              <div className="mt-4">
                <Link to={`/projects/${projectId}/keys`}>
                  <Button variant="secondary" size="sm" className="w-full">
                    Manage keys
                  </Button>
                </Link>
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
