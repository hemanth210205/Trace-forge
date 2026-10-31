export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 1000) return `${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

const pad = (n: number, width = 2) => String(n).padStart(width, "0");

/** Local time with milliseconds, e.g. ``2026-10-30 14:03:22.481``. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
  );
}

export function formatRelative(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86_400)}d ago`;
}

/** USD from the API's decimal string; up to 6 decimals, trailing zeros trimmed. */
export function formatCost(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "$0";
  return `$${n.toFixed(n >= 1 ? 2 : 6).replace(/0+$/, "").replace(/\.$/, "")}`;
}

export function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("en-US");
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

/** Offset of ``iso`` from ``startMs`` as ``+123 ms``. */
export function formatOffset(iso: string, startMs: number): string {
  return `+${formatDuration(new Date(iso).getTime() - startMs)}`;
}
