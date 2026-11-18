// Small presentational pieces shared by the dataset and evaluation pages.
import type { CaseState, RunStatus } from "../api/types";
import { cx } from "./ui";

const RUN_STYLE: Record<RunStatus, string> = {
  pending: "border-border bg-surface-raised text-slate-400",
  running: "border-sky-500/30 bg-sky-500/10 text-sky-400 animate-pulse",
  scoring: "border-sky-500/30 bg-sky-500/10 text-sky-400",
  completed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
  incomplete: "border-amber-500/30 bg-amber-500/10 text-amber-400",
  failed: "border-red-500/30 bg-red-500/10 text-red-400",
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className={cx("inline-flex items-center rounded border px-2 py-0.5 text-2xs font-medium capitalize", RUN_STYLE[status])}>
      {status}
    </span>
  );
}

const CASE_STYLE: Record<CaseState, { dot: string; text: string; label: string }> = {
  passed: { dot: "bg-emerald-400", text: "text-emerald-400", label: "Passed" },
  failed: { dot: "bg-red-400", text: "text-red-400", label: "Failed" },
  not_evaluated: { dot: "bg-slate-500", text: "text-slate-400", label: "Not evaluated" },
  missing: { dot: "bg-amber-400", text: "text-amber-400", label: "Missing" },
  pending: { dot: "border border-slate-500 bg-surface", text: "text-slate-400", label: "Pending" },
};

export function CaseStateBadge({ state }: { state: CaseState }) {
  const style = CASE_STYLE[state];
  return (
    <span className={cx("inline-flex items-center gap-1.5 text-xs font-medium", style.text)}>
      <span className={cx("h-1.5 w-1.5 shrink-0 rounded-full", style.dot)} aria-hidden />
      {style.label}
    </span>
  );
}

/** Pass/fail/not-applicable proportions as a thin stacked bar (counts are real run results). */
export function OutcomeBar({ passed, failed, other = 0 }: { passed: number; failed: number; other?: number }) {
  const total = passed + failed + other;
  if (!total) return <div className="h-1.5 w-full rounded bg-surface-raised" />;
  return (
    <div className="flex h-1.5 w-full overflow-hidden rounded bg-surface-raised" role="img" aria-label={`${passed} passed, ${failed} failed, ${other} other`}>
      <span className="bg-emerald-400" style={{ width: `${(passed / total) * 100}%` }} />
      <span className="bg-red-400" style={{ width: `${(failed / total) * 100}%` }} />
      <span className="bg-slate-600" style={{ width: `${(other / total) * 100}%` }} />
    </div>
  );
}

export function preview(value: unknown, max = 90): string {
  if (value === null || value === undefined) return "—";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

export function percent(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "—" : `${(rate * 100).toFixed(rate === 1 || rate === 0 ? 0 : 1)}%`;
}
