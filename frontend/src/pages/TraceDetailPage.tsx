import { useMemo, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, RefreshCw } from "lucide-react";

import { api, ApiError } from "../api/client";
import type { TraceDetail } from "../api/types";
import { useApi } from "../api/useApi";
import { SpanDetails, TraceOverview } from "../components/SpanDetails";
import { Timeline } from "../components/Timeline";
import { Button, Card, CopyButton, EmptyState, ErrorState, Spinner, StatusBadge } from "../components/ui";
import { formatCost, formatDuration, formatNumber, formatTimestamp } from "../lib/format";
import { ancestors, buildTimeline } from "../lib/spanTree";

export function TraceDetailPage() {
  const { projectId = "", traceId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const selectedId = params.get("span");
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const trace = useApi((signal) => api<TraceDetail>(`/projects/${projectId}/traces/${traceId}`, { signal }), [projectId, traceId]);
  const timeline = useMemo(() => (trace.data ? buildTimeline(trace.data.spans, trace.data) : null), [trace.data]);
  const selected = selectedId && timeline ? timeline.byId.get(selectedId)?.span ?? null : null;

  const listSearch = (location.state as { listSearch?: string } | null)?.listSearch;
  const back = (
    <Link
      to={`/projects/${projectId}/traces${listSearch ? `?${listSearch}` : ""}`}
      className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-400 hover:text-slate-100 transition-colors"
    >
      <ArrowLeft className="h-3.5 w-3.5" />
      Back to Traces
    </Link>
  );

  if (trace.error) {
    const missing = trace.error instanceof ApiError && trace.error.status === 404;
    return (
      <div className="space-y-4">
        {back}
        {missing ? (
          <EmptyState title="Trace not found">
            It does not exist in this project, or you do not have access to it.
          </EmptyState>
        ) : (
          <ErrorState error={trace.error} onRetry={trace.reload} />
        )}
      </div>
    );
  }
  if (!trace.data || !timeline) {
    return (
      <div className="py-12 text-center">
        <Spinner label="Loading trace" />
      </div>
    );
  }
  const t = trace.data;

  function select(spanId: string | null) {
    const next = new URLSearchParams(params);
    if (spanId) next.set("span", spanId);
    else next.delete("span");
    setParams(next, { replace: true, state: location.state });
    if (spanId && timeline) {
      const up = ancestors(timeline, spanId);
      if (up.some((id) => collapsed.has(id))) setCollapsed(new Set([...collapsed].filter((id) => !up.includes(id))));
    }
  }

  const parentName = selected?.parent_span_id ? timeline.byId.get(selected.parent_span_id)?.span.name ?? null : null;

  return (
    <div className="space-y-4">
      {/* Top actions bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
        {back}
        <Button variant="secondary" size="sm" onClick={trace.reload}>
          <RefreshCw className="h-3.5 w-3.5 text-slate-400" />
          <span>Refresh</span>
        </Button>
      </div>

      {/* Trace Summary Card */}
      <Card className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <StatusBadge status={t.status} />
          <h1 className="min-w-0 break-words text-lg font-bold text-slate-100 font-mono">{t.name}</h1>
          {t.tags.map((tag) => (
            <span key={tag} className="rounded border border-border bg-surface-raised px-2 py-0.5 font-mono text-2xs text-slate-300">
              {tag}
            </span>
          ))}
        </div>
        <p className="flex flex-wrap items-center gap-x-1 font-mono text-2xs text-slate-500">
          <span className="text-slate-400">{t.id}</span>
          <CopyButton value={t.id} label="Copy id" />
        </p>

        {!t.has_trace_record && (
          <p className="rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
            Only spans have been received for this trace; its trace record (name, status, input, output) has not arrived.
          </p>
        )}

        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 pt-2 text-xs sm:grid-cols-3 lg:grid-cols-7 border-t border-border">
          <Stat label="Duration" value={t.ended_at ? formatDuration(t.duration_ms) : "open"} />
          <Stat label="Started" value={formatTimestamp(t.started_at)} wide />
          <Stat label="Spans" value={formatNumber(t.span_count)} />
          <Stat label="Errors" value={formatNumber(t.error_count)} danger={t.error_count > 0} />
          <Stat label="Tokens" value={t.total_tokens ? formatNumber(t.total_tokens) : "—"} />
          <Stat label="Cost" value={Number(t.total_cost_usd) ? formatCost(t.total_cost_usd) : "—"} />
          <Stat label="User / session" value={[t.user_id, t.session_id].filter(Boolean).join(" · ") || "—"} />
        </dl>
      </Card>

      {/* Spans Timeline & Inspector */}
      {t.spans.length === 0 ? (
        <EmptyState title="No spans in this trace">The trace record arrived, but no spans were reported for it.</EmptyState>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_26rem] xl:grid-cols-[minmax(0,1fr)_32rem]">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
              <span className="font-mono">{t.spans.length} execution spans</span>
              {timeline.orphanCount > 0 && (
                <span className="text-amber-400 font-mono text-2xs">· {timeline.orphanCount} with a parent that was not received (shown at top level)</span>
              )}
              <div className="ml-auto flex gap-1.5">
                <Button variant="ghost" size="sm" onClick={() => setCollapsed(new Set())}>
                  Expand all
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setCollapsed(new Set([...timeline.byId.values()].filter((n) => n.children.length).map((n) => n.span.id)))
                  }
                >
                  Collapse all
                </Button>
              </div>
            </div>
            <Timeline
              timeline={timeline}
              collapsed={collapsed}
              selectedId={selected?.id ?? null}
              onSelect={(id) => select(id === selected?.id ? null : id)}
              onToggle={(id) => {
                const next = new Set(collapsed);
                if (next.has(id)) next.delete(id);
                else next.add(id);
                setCollapsed(next);
              }}
            />
          </div>

          {/* Inspector Panel */}
          <aside className="rounded-lg border border-border bg-surface lg:sticky lg:top-18 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto shadow-sm">
            {selected ? (
              <>
                <div className="flex items-center justify-between border-b border-border px-3 py-2 bg-surface-raised">
                  <span className="text-xs font-medium text-slate-400">Span Inspector</span>
                  <button
                    type="button"
                    className="text-xs text-slate-400 hover:text-slate-100 font-mono transition-colors"
                    onClick={() => select(null)}
                  >
                    Close ✕
                  </button>
                </div>
                <SpanDetails span={selected} traceStartMs={timeline.startMs} parentName={parentName} />
              </>
            ) : (
              <TraceOverview trace={t} />
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, wide, danger }: { label: string; value: string; wide?: boolean; danger?: boolean }) {
  return (
    <div className={wide ? "col-span-2 sm:col-span-1 lg:col-span-1" : undefined}>
      <dt className="text-xs font-medium text-slate-500">{label}</dt>
      <dd className={`mt-0.5 font-mono tabular-nums text-xs ${danger ? "font-semibold text-red-400" : "text-slate-200"}`}>{value}</dd>
    </div>
  );
}
