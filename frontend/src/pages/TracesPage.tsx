import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

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
    <div>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
        <h1 className="text-lg font-semibold">Traces</h1>
        <span className="text-sm text-ink-500">{project.data?.name}</span>
      </div>

      <form
        className="mb-3 flex flex-wrap items-center gap-2"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          update({ q: search.trim() || null });
        }}
        role="search"
      >
        <TextInput
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search name or exact trace id"
          aria-label="Search traces"
          className="w-full sm:w-72"
        />
        <SelectInput aria-label="Status" value={status} onChange={(e) => update({ status: e.target.value || null })}>
          <option value="">Any status</option>
          <option value="ok">OK</option>
          <option value="error">Error</option>
          <option value="unset">Unset</option>
        </SelectInput>
        <SelectInput aria-label="Time range" value={range} onChange={(e) => update({ range: e.target.value === "all" ? null : e.target.value })}>
          <option value="15m">Last 15 minutes</option>
          <option value="1h">Last hour</option>
          <option value="24h">Last 24 hours</option>
          <option value="7d">Last 7 days</option>
          <option value="all">All time</option>
        </SelectInput>
        <TextInput
          aria-label="Tag"
          placeholder="Tag"
          defaultValue={tag}
          key={tag}
          className="w-28"
          onBlur={(e) => update({ tag: e.target.value.trim() || null })}
        />
        <label className="flex items-center gap-1.5 text-sm text-ink-700">
          <input type="checkbox" checked={errorsOnly} onChange={(e) => update({ errors: e.target.checked ? "1" : null })} />
          Spans with errors
        </label>
        <Button type="submit">Search</Button>
        {filtered && (
          <Button type="button" variant="ghost" onClick={() => setParams(new URLSearchParams(), { replace: true })}>
            Clear filters
          </Button>
        )}
        <Button type="button" variant="ghost" className="ml-auto" onClick={page.reload} aria-label="Refresh">
          Refresh
        </Button>
      </form>

      {page.error && <ErrorState error={page.error} onRetry={page.reload} />}
      {page.loading && !page.data && (
        <div className="border border-ink-200 bg-white px-4 py-6">
          <Spinner label="Loading traces" />
        </div>
      )}
      {page.data && items.length === 0 && (
        filtered ? (
          <EmptyState title="No traces match these filters" action={<Button onClick={() => setParams(new URLSearchParams())}>Clear filters</Button>} />
        ) : (
          <EmptyState title="No traces yet" action={<Link className="text-sm underline" to={`/projects/${projectId}/keys`}>Create an API key</Link>}>
            Instrument your agent with the Python SDK and send a trace:{" "}
            <code className="font-mono text-xs">traceforge.init(api_key=…)</code>, then wrap a run in{" "}
            <code className="font-mono text-xs">with traceforge.trace("name"):</code>.
          </EmptyState>
        )
      )}
      {items.length > 0 && (
        <div className={cx("border border-ink-200 bg-white", page.loading && "opacity-60")}>
          <table className="w-full table-fixed text-sm">
            <thead className="border-b border-ink-200 bg-ink-50 text-left text-2xs uppercase tracking-wider text-ink-500">
              <tr>
                <th className="w-8 px-3 py-2" aria-label="Trace status" />
                <th className="px-3 py-2 font-semibold">Trace</th>
                <th className="hidden w-44 px-3 py-2 font-semibold md:table-cell">Started</th>
                <th className="w-24 px-3 py-2 text-right font-semibold">Duration</th>
                <th className="hidden w-20 px-3 py-2 text-right font-semibold sm:table-cell">Spans</th>
                <th className="hidden w-20 px-3 py-2 text-right font-semibold lg:table-cell">Errors</th>
                <th className="hidden w-24 px-3 py-2 text-right font-semibold lg:table-cell">Tokens</th>
                <th className="hidden w-24 px-3 py-2 text-right font-semibold xl:table-cell">Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {items.map((trace) => (
                <TraceRow key={trace.id} projectId={projectId} trace={trace} listSearch={params.toString()} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {moreError && <div className="mt-2"><ErrorState error={moreError} onRetry={() => void loadMore()} /></div>}
      {nextCursor && (
        <div className="mt-3 text-center">
          <Button onClick={() => void loadMore()} disabled={loadingMore}>
            {loadingMore ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}
      {items.length > 0 && !nextCursor && <p className="mt-2 text-center text-xs text-ink-500">{items.length} traces · end of results</p>}
    </div>
  );
}

function TraceRow({ projectId, trace, listSearch }: { projectId: string; trace: TraceSummary; listSearch: string }) {
  return (
    <tr className="group hover:bg-ink-50">
      <td className="px-3 py-2 align-top">
        <StatusBadge status={trace.status} compact />
      </td>
      <td className="min-w-0 px-3 py-2">
        <Link to={`/projects/${projectId}/traces/${trace.id}`} state={{ listSearch }} className="block truncate font-medium text-ink-900 group-hover:underline">
          {trace.name}
        </Link>
        <div className="flex flex-wrap items-center gap-x-2 text-2xs text-ink-500">
          <span className="font-mono">{shortId(trace.id)}</span>
          <span className="md:hidden">{formatRelative(trace.started_at)}</span>
          {trace.user_id && <span>user {trace.user_id}</span>}
          {!trace.has_trace_record && <span className="text-amber-700">trace record not received</span>}
          {trace.tags.map((t) => (
            <span key={t} className="rounded border border-ink-200 px-1">{t}</span>
          ))}
        </div>
      </td>
      <td className="hidden px-3 py-2 align-top text-xs text-ink-700 md:table-cell" title={formatTimestamp(trace.started_at)}>
        <span className="font-mono">{formatTimestamp(trace.started_at).slice(5, 19)}</span>
        <span className="block text-2xs text-ink-500">{formatRelative(trace.started_at)}</span>
      </td>
      <td className="px-3 py-2 text-right align-top font-mono text-xs tabular-nums">
        {trace.ended_at ? formatDuration(trace.duration_ms) : <span className="text-ink-500">open</span>}
      </td>
      <td className="hidden px-3 py-2 text-right align-top font-mono text-xs tabular-nums sm:table-cell">{trace.span_count}</td>
      <td className={cx("hidden px-3 py-2 text-right align-top font-mono text-xs tabular-nums lg:table-cell", trace.error_count > 0 && "font-semibold text-red-700")}>
        {trace.error_count}
      </td>
      <td className="hidden px-3 py-2 text-right align-top font-mono text-xs tabular-nums lg:table-cell">
        {trace.total_tokens ? formatNumber(trace.total_tokens) : "—"}
      </td>
      <td className="hidden px-3 py-2 text-right align-top font-mono text-xs tabular-nums xl:table-cell">
        {Number(trace.total_cost_usd) ? formatCost(trace.total_cost_usd) : "—"}
      </td>
    </tr>
  );
}
