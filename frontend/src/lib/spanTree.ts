import type { Span } from "../api/types";

export interface SpanNode {
  span: Span;
  children: SpanNode[];
  depth: number;
  /** The span names a parent that was never received (or is itself / part of a cycle). */
  orphan: boolean;
}

export interface Timeline {
  roots: SpanNode[];
  byId: Map<string, SpanNode>;
  startMs: number;
  endMs: number;
  totalMs: number;
  orphanCount: number;
}

const time = (iso: string) => new Date(iso).getTime();

function byStart(a: SpanNode, b: SpanNode): number {
  return time(a.span.started_at) - time(b.span.started_at) || a.span.name.localeCompare(b.span.name);
}

/**
 * Builds the span hierarchy. Spans are sent when they end, so parents can be missing (agent crash, dropped
 * batch); such spans become roots flagged ``orphan`` instead of disappearing. Parent cycles, which the
 * backend cannot rule out, are broken the same way.
 */
export function buildTimeline(spans: Span[], trace?: { started_at: string; ended_at: string | null }): Timeline {
  const byId = new Map<string, SpanNode>();
  for (const span of spans) byId.set(span.id, { span, children: [], depth: 0, orphan: false });

  const roots: SpanNode[] = [];
  for (const node of byId.values()) {
    const parentId = node.span.parent_span_id;
    const parent = parentId && parentId !== node.span.id ? byId.get(parentId) : undefined;
    if (parent) parent.children.push(node);
    else {
      node.orphan = parentId !== null;
      roots.push(node);
    }
  }

  // Depth-first from the roots; anything not reached is in a cycle: detach it and surface it as an orphan.
  const visited = new Set<string>();
  const walk = (node: SpanNode, depth: number) => {
    visited.add(node.span.id);
    node.depth = depth;
    node.children.sort(byStart);
    for (const child of node.children) walk(child, depth + 1);
  };
  roots.sort(byStart).forEach((root) => walk(root, 0));
  for (const node of byId.values()) {
    if (visited.has(node.span.id)) continue;
    const parent = node.span.parent_span_id ? byId.get(node.span.parent_span_id) : undefined;
    if (parent) parent.children = parent.children.filter((c) => c !== node);
    node.orphan = true;
    roots.push(node);
    walk(node, 0);
  }
  roots.sort(byStart);

  const starts = spans.map((s) => time(s.started_at));
  const ends = spans.map((s) => time(s.ended_at ?? s.started_at));
  if (trace) {
    starts.push(time(trace.started_at));
    if (trace.ended_at) ends.push(time(trace.ended_at));
  }
  const startMs = starts.length ? Math.min(...starts) : 0;
  const endMs = Math.max(startMs, ...ends);
  return {
    roots,
    byId,
    startMs,
    endMs,
    totalMs: Math.max(endMs - startMs, 1),
    orphanCount: [...byId.values()].filter((n) => n.orphan).length,
  };
}

/** Rows in display order, skipping descendants of collapsed spans. */
export function visibleRows(timeline: Timeline, collapsed: ReadonlySet<string>): SpanNode[] {
  const rows: SpanNode[] = [];
  const add = (node: SpanNode) => {
    rows.push(node);
    if (!collapsed.has(node.span.id)) node.children.forEach(add);
  };
  timeline.roots.forEach(add);
  return rows;
}

export interface BarGeometry {
  leftPct: number;
  widthPct: number;
  /** No end time: drawn to the end of the trace window. */
  open: boolean;
}

export function barGeometry(span: Span, timeline: Timeline): BarGeometry {
  const start = time(span.started_at);
  const open = span.ended_at === null;
  const end = open ? timeline.endMs : time(span.ended_at as string);
  const leftPct = ((start - timeline.startMs) / timeline.totalMs) * 100;
  const widthPct = Math.max(((end - start) / timeline.totalMs) * 100, 0.25);
  return { leftPct: Math.min(leftPct, 99.75), widthPct: Math.min(widthPct, 100 - Math.min(leftPct, 99.75)), open };
}

/** Ancestor ids of a span (nearest first), used to expand collapsed branches when selecting it. */
export function ancestors(timeline: Timeline, spanId: string): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  let node = timeline.byId.get(spanId);
  while (node?.span.parent_span_id && !node.orphan && !seen.has(node.span.id)) {
    seen.add(node.span.id);
    const parent = timeline.byId.get(node.span.parent_span_id);
    if (!parent) break;
    result.push(parent.span.id);
    node = parent;
  }
  return result;
}
