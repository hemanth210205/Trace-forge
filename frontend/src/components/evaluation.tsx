// Small presentational pieces shared by the dataset and evaluation pages.
import type { CaseState, RunStatus } from "../api/types";
import { cx } from "./ui";

const RUN_STYLE: Record<RunStatus, string> = {
  pending: "border-ink-200 bg-white text-ink-700",
  running: "border-sky-300 bg-sky-50 text-sky-800",
  scoring: "border-sky-300 bg-sky-50 text-sky-800",
  completed: "border-emerald-300 bg-emerald-50 text-emerald-800",
  incomplete: "border-amber-300 bg-amber-50 text-amber-900",
  failed: "border-red-300 bg-red-50 text-red-800",
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className={cx("inline-flex items-center rounded border px-1.5 font-mono text-2xs uppercase tracking-wide", RUN_STYLE[status])}>
      {status}
    </span>
  );
}

const CASE_STYLE: Record<CaseState, { dot: string; text: string; label: string }> = {
  passed: { dot: "bg-emerald-600", text: "text-emerald-700", label: "Passed" },
  failed: { dot: "bg-red-600", text: "text-red-700", label: "Failed" },
  not_evaluated: { dot: "bg-ink-300", text: "text-ink-500", label: "Not evaluated" },
  missing: { dot: "bg-amber-500", text: "text-amber-800", label: "Missing" },
  pending: { dot: "border border-ink-300 bg-white", text: "text-ink-500", label: "Pending" },
};

export function CaseStateBadge({ state }: { state: CaseState }) {
  const style = CASE_STYLE[state];
  return (
    <span className={cx("inline-flex items-center gap-1.5 text-xs font-medium", style.text)}>
      <span className={cx("h-2 w-2 shrink-0 rounded-full", style.dot)} aria-hidden />
      {style.label}
    </span>
  );
}

/** Pass/fail/not-applicable proportions as a thin stacked bar (counts are real run results). */
export function OutcomeBar({ passed, failed, other = 0 }: { passed: number; failed: number; other?: number }) {
  const total = passed + failed + other;
  if (!total) return <div className="h-1.5 w-full bg-ink-100" />;
  return (
    <div className="flex h-1.5 w-full overflow-hidden bg-ink-100" role="img" aria-label={`${passed} passed, ${failed} failed, ${other} other`}>
      <span className="bg-emerald-600" style={{ width: `${(passed / total) * 100}%` }} />
      <span className="bg-red-600" style={{ width: `${(failed / total) * 100}%` }} />
      <span className="bg-ink-300" style={{ width: `${(other / total) * 100}%` }} />
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
