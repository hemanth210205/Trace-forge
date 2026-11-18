import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ListTree, RefreshCw, Search } from "lucide-react";

import { api } from "../api/client";
import type { Project, TracePage, TraceSummary } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, EmptyState, ErrorState, SelectInput, Spinner, StatusBadge, TextInput, cx } from "../components/ui";
import { formatCost, formatDuration, formatNumber, formatRelative, formatTimestamp, shortId } from "../lib/format";

const RANGES: Record<string, number | null> = { "15m": 15 * 60e3, "1h": 3600e3, "24h": 86_400e3, "7d": 7 * 86_400e3, all: null };

export function TracesPage() {
  const { projectId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const q = params.get("q") ?? "";
  const tag = params.get("tag") ?? "";
  const errorsOnly = params.get("errors") === "1";
  const range = params.get("range") ?? "all";
  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);

  const project = useApi((signal) => api<Project>(`/projects/${projectId}`, { signal }), [projectId]);
  const [extra, setExtra] = useState<{ items: TraceSummary[]; cursor: string | null } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<Error | null>(null);

  const query = (cursor?: string | null) => {
    const windowMs = RANGES[range];
    return {
      status: status || undefined,
      q: q || undefined,
      tag: tag || undefined,
      has_errors: errorsOnly ? true : undefined,
      since: windowMs ? new Date(Date.now() - windowMs).toISOString() : undefined,
      limit: 50,
      cursor: cursor ?? undefined,
    };
  };
  const page = useApi(
    (signal) => api<TracePage>(`/projects/${projectId}/traces`, { query: query(), signal }),
    [projectId, status, q, tag, errorsOnly, range],
  );
  useEffect(() => setExtra(null), [page.data]);

  const items = [...(page.data?.items ?? []), ...(extra?.items ?? [])];
  const nextCursor = extra ? extra.cursor : page.data?.next_cursor ?? null;
  const filtered = Boolean(status || q || tag || errorsOnly || range !== "all");

  function update(changes: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  }

  async function loadMore() {
    if (!nextCursor) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const more = await api<TracePage>(`/projects/${projectId}/traces`, { query: query(nextCursor) });
      setExtra((prev) => ({ items: [...(prev?.items ?? []), ...more.items], cursor: more.next_cursor }));
    } catch (err) {
      setMoreError(err as Error);
    } finally {
      setLoadingMore(false);
    }
  }

  if (project.error) return <ErrorState error={project.error} onRetry={project.reload} />;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between border-b border-border pb-3">
        <div>
          <div className="flex items-center gap-2">
            <ListTree className="h-5 w-5 text-slate-500" />
            <h1 className="text-xl font-semibold tracking-tight text-slate-100">Traces</h1>
            <span className="text-xs text-slate-400 font-normal">({project.data?.name})</span>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Every run your agent sends, newest first. Open one to see each step it took.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={page.reload}
            aria-label="Refresh"
            disabled={page.loading}
          >
            <RefreshCw className={cx("h-3.5 w-3.5", page.loading && "animate-spin text-accent")} />
            <span>Refresh</span>
          </Button>
        </div>
      </div>

      {/* Filter / Search Bar */}
      <form
        className="flex flex-wrap items-center gap-2.5 rounded-lg border border-border bg-surface p-3"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          update({ q: search.trim() || null });
        }}
        role="search"
      >
        <div className="relative flex-1 min-w-[200px] sm:max-w-xs">
          <TextInput
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search trace name or exact ID..."
            aria-label="Search traces"
            className="w-full pl-8"
          />
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-500" />
        </div>

        <SelectInput aria-label="Status" value={status} onChange={(e) => update({ status: e.target.value || null })}>
          <option value="">Any Status</option>
          <option value="ok">OK</option>
          <option value="error">Error</option>
          <option value="unset">Unset</option>
        </SelectInput>

        <SelectInput
          aria-label="Time range"
          value={range}
          onChange={(e) => update({ range: e.target.value === "all" ? null : e.target.value })}
        >
          <option value="15m">Last 15m</option>
          <option value="1h">Last 1h</option>
          <option value="24h">Last 24h</option>
          <option value="7d">Last 7d</option>
          <option value="all">All time</option>
        </SelectInput>

        <TextInput
          aria-label="Tag"
          placeholder="Filter tag..."
          defaultValue={tag}
          key={tag}
          className="w-32"
          onBlur={(e) => update({ tag: e.target.value.trim() || null })}
        />

        <label className="flex items-center gap-2 text-xs font-medium text-slate-300 cursor-pointer select-none">
          <input
            type="checkbox"
            className="rounded border-border bg-surface text-accent focus:ring-accent"
            checked={errorsOnly}
            onChange={(e) => update({ errors: e.target.checked ? "1" : null })}
          />
          Spans with errors
        </label>

        <Button type="submit" variant="secondary" size="sm">
          Filter
        </Button>

        {filtered && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setParams(new URLSearchParams(), { replace: true })}
          >
            Clear filters
          </Button>
        )}
      </form>

      {/* Content State */}
      {page.error && <ErrorState error={page.error} onRetry={page.reload} />}

      {page.loading && !page.data && (
        <div className="rounded-lg border border-border bg-surface p-12 text-center">
          <Spinner label="Loading traces" />
        </div>
      )}

      {page.data && items.length === 0 && (
        filtered ? (
          <EmptyState
            title="No traces match these filters"
            action={<Button onClick={() => setParams(new URLSearchParams())}>Clear filters</Button>}
          />
        ) : (
          <EmptyState
            title="No traces yet"
            action={
              <Link className="text-sm underline text-accent" to={`/projects/${projectId}/keys`}>
                Create an API key
              </Link>
            }
          >
            Instrument your agent with the Python SDK and send a trace:{" "}
            <code className="font-mono text-xs text-slate-200">traceforge.init(api_key=…)</code>, then wrap a run in{" "}
            <code className="font-mono text-xs text-slate-200">with traceforge.trace("name"):</code>.
          </EmptyState>
        )
      )}

      {items.length > 0 && (
        <div className={cx("overflow-hidden rounded-lg border border-border bg-surface shadow-sm", page.loading && "opacity-60")}>
          <table className="w-full table-fixed text-sm border-collapse">
            <thead className="border-b border-border bg-surface-raised text-left text-xs font-medium text-slate-400">
              <tr>
                <th className="w-9 px-3 py-2.5" aria-label="Trace status" />
                <th className="px-3 py-2.5 font-semibold text-slate-300">Trace Name & ID</th>
                <th className="hidden w-44 px-3 py-2.5 font-semibold text-slate-300 md:table-cell">Started</th>
                <th className="w-24 px-3 py-2.5 text-right font-semibold text-slate-300">Duration</th>
                <th className="hidden w-20 px-3 py-2.5 text-right font-semibold text-slate-300 sm:table-cell">Spans</th>
                <th className="hidden w-20 px-3 py-2.5 text-right font-semibold text-slate-300 lg:table-cell">Errors</th>
                <th className="hidden w-24 px-3 py-2.5 text-right font-semibold text-slate-300 lg:table-cell">Tokens</th>
                <th className="hidden w-24 px-3 py-2.5 text-right font-semibold text-slate-300 xl:table-cell">Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((trace) => (
                <TraceRow key={trace.id} projectId={projectId} trace={trace} listSearch={params.toString()} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {moreError && (
        <div className="mt-2">
          <ErrorState error={moreError} onRetry={() => void loadMore()} />
        </div>
      )}

      {nextCursor && (
        <div className="mt-3 text-center">
          <Button onClick={() => void loadMore()} disabled={loadingMore} variant="secondary">
            {loadingMore ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}

      {items.length > 0 && !nextCursor && (
        <p className="mt-2 text-center text-xs text-slate-500 font-mono">
          {items.length} traces · end of results
        </p>
      )}
    </div>
  );
}

function TraceRow({ projectId, trace, listSearch }: { projectId: string; trace: TraceSummary; listSearch: string }) {
  return (
    <tr className="group hover:bg-surface-hover/80 transition-colors">
      <td className="px-3 py-2.5 align-top">
        <StatusBadge status={trace.status} compact />
      </td>
      <td className="min-w-0 px-3 py-2.5">
        <Link
          to={`/projects/${projectId}/traces/${trace.id}`}
          state={{ listSearch }}
          className="block truncate font-medium text-slate-100 group-hover:text-accent-400 group-hover:underline transition-colors"
        >
          {trace.name}
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 text-2xs text-slate-400">
          <span className="font-mono text-slate-500">{shortId(trace.id)}</span>
          <span className="md:hidden text-slate-500">{formatRelative(trace.started_at)}</span>
          {trace.user_id && <span className="text-slate-400">user {trace.user_id}</span>}
          {!trace.has_trace_record && <span className="text-amber-400">trace record not received</span>}
          {trace.tags.map((t) => (
            <span key={t} className="rounded border border-border bg-surface-raised px-1.5 py-0.2 font-mono text-slate-300">
              {t}
            </span>
          ))}
        </div>
      </td>
      <td className="hidden px-3 py-2.5 align-top text-xs text-slate-300 md:table-cell" title={formatTimestamp(trace.started_at)}>
        <span className="font-mono text-slate-200">{formatTimestamp(trace.started_at).slice(5, 19)}</span>
        <span className="block text-2xs text-slate-500">{formatRelative(trace.started_at)}</span>
      </td>
      <td className="px-3 py-2.5 text-right align-top font-mono text-xs tabular-nums text-slate-200">
        {trace.ended_at ? formatDuration(trace.duration_ms) : <span className="text-slate-500">open</span>}
      </td>
      <td className="hidden px-3 py-2.5 text-right align-top font-mono text-xs tabular-nums text-slate-300 sm:table-cell">
        {trace.span_count}
      </td>
      <td className={cx("hidden px-3 py-2.5 text-right align-top font-mono text-xs tabular-nums lg:table-cell", trace.error_count > 0 ? "font-semibold text-red-700" : "text-slate-400")}>
        {trace.error_count}
      </td>
      <td className="hidden px-3 py-2.5 text-right align-top font-mono text-xs tabular-nums text-slate-300 lg:table-cell">
        {trace.total_tokens ? formatNumber(trace.total_tokens) : "—"}
      </td>
      <td className="hidden px-3 py-2.5 text-right align-top font-mono text-xs tabular-nums text-slate-300 xl:table-cell">
        {Number(trace.total_cost_usd) ? formatCost(trace.total_cost_usd) : "—"}
      </td>
    </tr>
  );
}
