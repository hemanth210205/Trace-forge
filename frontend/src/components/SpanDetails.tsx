import type { ReactNode } from "react";

import type { Span, TraceDetail } from "../api/types";
import { formatCost, formatDuration, formatNumber, formatOffset, formatTimestamp } from "../lib/format";
import { JsonView } from "./JsonView";
import { CopyButton, KindTag, SectionTitle, StatusBadge } from "./ui";

interface RetrievedDocument {
  id?: unknown;
  source?: unknown;
  score?: unknown;
  content?: unknown;
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-ink-500">{label}</dt>
      <dd className="min-w-0 break-words text-ink-900">{children}</dd>
    </>
  );
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="border-t border-ink-100 px-4 py-3">
      <SectionTitle aside={aside}>{title}</SectionTitle>
      {children}
    </section>
  );
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;

export function SpanDetails({ span, traceStartMs, parentName }: { span: Span; traceStartMs: number; parentName: string | null }) {
  const attributes = { ...span.attributes };
  const documents = Array.isArray(attributes.documents) ? (attributes.documents as RetrievedDocument[]) : null;
  const tool = asRecord(attributes.tool);
  const llmParams = asRecord(attributes.llm);
  delete attributes.documents;
  delete attributes.tool;
  delete attributes.llm;
  const hasLlm = span.model !== null || span.prompt_tokens !== null || span.completion_tokens !== null || span.total_tokens !== null || span.cost_usd !== null || llmParams !== null;

  return (
    <div aria-label={`Span ${span.name}`}>
      <header className="px-4 pb-3 pt-3.5">
        <div className="flex flex-wrap items-center gap-2">
          <KindTag kind={span.kind} />
          <StatusBadge status={span.status} />
        </div>
        <h2 className="mt-1.5 break-words text-base font-semibold">{span.name}</h2>
        <dl className="mt-2 grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1 text-xs">
          <Row label="Duration">
            <span className="font-mono tabular-nums">{span.ended_at ? formatDuration(span.duration_ms) : "not ended"}</span>
          </Row>
          <Row label="Starts at">
            <span className="font-mono tabular-nums">{formatOffset(span.started_at, traceStartMs)}</span>
            <span className="ml-2 text-ink-500">{formatTimestamp(span.started_at)}</span>
          </Row>
          <Row label="Span id">
            <span className="font-mono">{span.id}</span> <CopyButton value={span.id} />
          </Row>
          <Row label="Parent">
            {span.parent_span_id ? (
              parentName ? parentName : <span className="text-amber-700">not received ({span.parent_span_id.slice(0, 8)})</span>
            ) : (
              <span className="text-ink-500">root</span>
            )}
          </Row>
        </dl>
      </header>

      {span.error && (
        <Section title="Error">
          <div className="border border-red-200 bg-red-50 p-2.5 text-sm text-red-900">
            <p className="font-mono text-xs font-semibold">{span.error.type}</p>
            {span.error.message && <p className="mt-0.5 break-words">{span.error.message}</p>}
            {span.error.stacktrace && (
              <details className="mt-2">
                <summary className="cursor-pointer text-xs text-red-800">Stack trace</summary>
                <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-2xs">{span.error.stacktrace}</pre>
              </details>
            )}
          </div>
        </Section>
      )}

      {hasLlm && (
        <Section title="Model call">
          <dl className="grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1 text-xs">
            <Row label="Model">{span.model ? <span className="font-mono">{span.model}</span> : <span className="text-ink-500">not reported</span>}</Row>
            <Row label="Tokens">
              <span className="font-mono tabular-nums">
                {formatNumber(span.prompt_tokens)} in · {formatNumber(span.completion_tokens)} out · {formatNumber(span.total_tokens)} total
              </span>
            </Row>
            <Row label="Cost">
              <span className="font-mono tabular-nums">{formatCost(span.cost_usd)}</span>
            </Row>
            {llmParams &&
              Object.entries(llmParams).map(([key, value]) => (
                <Row key={key} label={key}>
                  <span className="font-mono">{typeof value === "string" ? value : JSON.stringify(value)}</span>
                </Row>
              ))}
          </dl>
        </Section>
      )}

      {tool && (
        <Section title="Tool call">
          <dl className="grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1 text-xs">
            <Row label="Tool">
              <span className="font-mono">{String(tool.name ?? "unnamed")}</span>
            </Row>
          </dl>
          {"arguments" in tool && (
            <div className="mt-2">
              <JsonView value={tool.arguments} />
            </div>
          )}
        </Section>
      )}

      {documents && (
        <Section title={`Retrieved documents (${documents.length})`}>
          {documents.length === 0 ? (
            <p className="text-xs italic text-ink-500">No documents returned.</p>
          ) : (
            <ol className="divide-y divide-ink-100 border border-ink-200">
              {documents.map((doc, i) => (
                <li key={i} className="px-2.5 py-2 text-xs">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="min-w-0 break-all font-mono text-ink-900">{String(doc.source ?? doc.id ?? `document ${i + 1}`)}</span>
                    {typeof doc.score === "number" && <span className="shrink-0 font-mono tabular-nums text-ink-500">score {doc.score.toFixed(3)}</span>}
                  </div>
                  {doc.source !== undefined && doc.id !== undefined && <p className="font-mono text-2xs text-ink-500">id {String(doc.id)}</p>}
                  {typeof doc.content === "string" && <p className="mt-1 line-clamp-4 text-ink-700">{doc.content}</p>}
                </li>
              ))}
            </ol>
          )}
        </Section>
      )}

      <Section title="Input">
        <JsonView value={span.input} />
      </Section>
      <Section title="Output">
        <JsonView value={span.output} />
      </Section>
      {Object.keys(attributes).length > 0 && (
        <Section title="Attributes">
          <JsonView value={attributes} />
        </Section>
      )}
      {Object.keys(span.metadata).length > 0 && (
        <Section title="Metadata">
          <JsonView value={span.metadata} />
        </Section>
      )}
    </div>
  );
}

export function TraceOverview({ trace }: { trace: TraceDetail }) {
  return (
    <div aria-label="Trace details">
      <header className="px-4 pb-3 pt-3.5">
        <p className="text-2xs font-semibold uppercase tracking-wider text-ink-500">Trace</p>
        <h2 className="mt-1 break-words text-base font-semibold">{trace.name}</h2>
        <p className="mt-1 text-xs text-ink-500">Select a span in the timeline to inspect it.</p>
      </header>
      <Section title="Input">
        <JsonView value={trace.input} />
      </Section>
      <Section title="Output">
        <JsonView value={trace.output} />
      </Section>
      <Section title="Metadata">
        <JsonView value={trace.metadata} emptyLabel="None" />
      </Section>
    </div>
  );
}
