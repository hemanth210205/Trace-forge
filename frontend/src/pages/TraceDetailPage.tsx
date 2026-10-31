import { useMemo, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { TraceDetail } from "../api/types";
import { useApi } from "../api/useApi";
import { SpanDetails, TraceOverview } from "../components/SpanDetails";
import { Timeline } from "../components/Timeline";
import { Button, CopyButton, EmptyState, ErrorState, Spinner, StatusBadge } from "../components/ui";
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

  // Return to the list with the filters it had when this trace was opened.
  const listSearch = (location.state as { listSearch?: string } | null)?.listSearch;
  const back = (
    <Link to={`/projects/${projectId}/traces${listSearch ? `?${listSearch}` : ""}`} className="text-sm text-ink-500 hover:text-ink-900">
      ← Traces
    </Link>
  );

  if (trace.error) {
    const missing = trace.error instanceof ApiError && trace.error.status === 404;
    return (
      <div className="space-y-3">
        {back}
        {missing ? <EmptyState title="Trace not found">It does not exist in this project, or you do not have access to it.</EmptyState> : <ErrorState error={trace.error} onRetry={trace.reload} />}
      </div>
    );
  }
  if (!trace.data || !timeline) return <Spinner label="Loading trace" />;
  const t = trace.data;

  function select(spanId: string | null) {
    const next = new URLSearchParams(params);
    if (spanId) next.set("span", spanId);
    else next.delete("span");
    setParams(next, { replace: true, state: location.state }); // keep the list filters for the back link
    if (spanId && timeline) {
      // Expand any collapsed ancestors so the selection is visible.
      const up = ancestors(timeline, spanId);
      if (up.some((id) => collapsed.has(id))) setCollapsed(new Set([...collapsed].filter((id) => !up.includes(id))));
    }
  }

  const parentName = selected?.parent_span_id ? timeline.byId.get(selected.parent_span_id)?.span.name ?? null : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        {back}
        <Button variant="ghost" className="ml-auto h-7 text-xs" onClick={trace.reload}>
          Refresh
        </Button>
      </div>

      <section className="border border-ink-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <StatusBadge status={t.status} />
          <h1 className="min-w-0 break-words text-lg font-semibold">{t.name}</h1>
          {t.tags.map((tag) => (
            <span key={tag} className="rounded border border-ink-200 px-1.5 text-2xs text-ink-700">{tag}</span>
          ))}
        </div>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1 font-mono text-2xs text-ink-500">
          {t.id} <CopyButton value={t.id} label="Copy id" />
        </p>
        {!t.has_trace_record && (
          <p className="mt-2 border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900">
            Only spans have been received for this trace; its trace record (name, status, input, output) has not arrived.
          </p>
        )}
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3 lg:grid-cols-7">
          <Stat label="Duration" value={t.ended_at ? formatDuration(t.duration_ms) : "open"} />
          <Stat label="Started" value={formatTimestamp(t.started_at)} wide />
          <Stat label="Spans" value={formatNumber(t.span_count)} />
          <Stat label="Errors" value={formatNumber(t.error_count)} danger={t.error_count > 0} />
          <Stat label="Tokens" value={t.total_tokens ? formatNumber(t.total_tokens) : "—"} />
          <Stat label="Cost" value={Number(t.total_cost_usd) ? formatCost(t.total_cost_usd) : "—"} />
          <Stat label="User / session" value={[t.user_id, t.session_id].filter(Boolean).join(" · ") || "—"} />
        </dl>
      </section>

      {t.spans.length === 0 ? (
        <EmptyState title="No spans in this trace">The trace record arrived, but no spans were reported for it.</EmptyState>
      ) : (
        <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_26rem] xl:grid-cols-[minmax(0,1fr)_30rem]">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
              <span>{t.spans.length} spans</span>
              {timeline.orphanCount > 0 && (
                <span className="text-amber-700">· {timeline.orphanCount} with a parent that was not received (shown at top level)</span>
              )}
              <span className="ml-auto flex gap-1">
                <Button variant="ghost" className="h-6 px-2 text-xs" onClick={() => setCollapsed(new Set())}>
                  Expand all
                </Button>
                <Button
                  variant="ghost"
                  className="h-6 px-2 text-xs"
                  onClick={() => setCollapsed(new Set([...timeline.byId.values()].filter((n) => n.children.length).map((n) => n.span.id)))}
                >
                  Collapse all
                </Button>
              </span>
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
          <aside className="border border-ink-200 bg-white lg:sticky lg:top-16 lg:max-h-[calc(100vh-5rem)] lg:overflow-y-auto">
            {selected ? (
              <>
                <div className="flex justify-end border-b border-ink-100 px-2 py-1">
                  <button type="button" className="text-xs text-ink-500 hover:text-ink-900" onClick={() => select(null)}>
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
      <dt className="text-2xs uppercase tracking-wider text-ink-500">{label}</dt>
      <dd className={`mt-0.5 font-mono tabular-nums ${danger ? "font-semibold text-red-700" : "text-ink-900"}`}>{value}</dd>
    </div>
  );
}
