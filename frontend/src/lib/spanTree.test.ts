import { describe, expect, it } from "vitest";

import type { Span } from "../api/types";
import { ancestors, barGeometry, buildTimeline, visibleRows } from "./spanTree";

const T0 = Date.parse("2026-10-30T12:00:00.000Z");
const at = (ms: number) => new Date(T0 + ms).toISOString();

function span(id: string, parent: string | null, start: number, end: number | null, extra: Partial<Span> = {}): Span {
  return {
    id, trace_id: "t", parent_span_id: parent, name: id, kind: "custom", status: "ok",
    started_at: at(start), ended_at: end === null ? null : at(end), duration_ms: end === null ? null : end - start,
    input: null, output: null, metadata: {}, attributes: {}, error: null, model: null,
    prompt_tokens: null, completion_tokens: null, total_tokens: null, cost_usd: null, ...extra,
  };
}

describe("buildTimeline", () => {
  it("nests children under parents, ordered by start time", () => {
    const tl = buildTimeline([span("c2", "root", 50, 60), span("root", null, 0, 100), span("c1", "root", 10, 40), span("g", "c1", 12, 20)]);
    expect(tl.roots.map((n) => n.span.id)).toEqual(["root"]);
    expect(visibleRows(tl, new Set()).map((n) => [n.span.id, n.depth])).toEqual([["root", 0], ["c1", 1], ["g", 2], ["c2", 1]]);
    expect(tl.orphanCount).toBe(0);
  });

  it("keeps spans whose parent was never received as flagged top-level orphans", () => {
    const tl = buildTimeline([span("root", null, 0, 100), span("lost", "missing-parent", 30, 40), span("kid", "lost", 31, 35)]);
    const lost = tl.byId.get("lost")!;
    expect(lost.orphan).toBe(true);
    expect(tl.roots.map((n) => n.span.id)).toEqual(["root", "lost"]);
    expect(lost.children.map((n) => n.span.id)).toEqual(["kid"]);
    expect(tl.orphanCount).toBe(1);
  });

  it("breaks parent cycles and self-parenting instead of looping or dropping spans", () => {
    const tl = buildTimeline([span("a", "b", 0, 10), span("b", "a", 1, 5), span("self", "self", 2, 3)]);
    const rows = visibleRows(tl, new Set());
    expect(rows.map((n) => n.span.id).sort()).toEqual(["a", "b", "self"]);
    expect(tl.byId.get("self")!.orphan).toBe(true);
    expect(rows.filter((n) => n.orphan).length).toBeGreaterThanOrEqual(2);
  });

  it("uses the trace window and draws open spans to the end", () => {
    const tl = buildTimeline([span("s", null, 100, 200), span("open", null, 150, null)], { started_at: at(0), ended_at: at(400) });
    expect(tl.totalMs).toBe(400);
    expect(barGeometry(tl.byId.get("s")!.span, tl)).toEqual({ leftPct: 25, widthPct: 25, open: false });
    const open = barGeometry(tl.byId.get("open")!.span, tl);
    expect(open.open).toBe(true);
    expect(open.leftPct + open.widthPct).toBeCloseTo(100);
  });

  it("gives zero-length spans a visible minimum width", () => {
    const tl = buildTimeline([span("root", null, 0, 1000), span("instant", "root", 500, 500)]);
    expect(barGeometry(tl.byId.get("instant")!.span, tl).widthPct).toBeGreaterThan(0);
  });

  it("collapses branches and lists ancestors", () => {
    const tl = buildTimeline([span("root", null, 0, 100), span("c", "root", 1, 50), span("g", "c", 2, 10)]);
    expect(visibleRows(tl, new Set(["c"])).map((n) => n.span.id)).toEqual(["root", "c"]);
    expect(ancestors(tl, "g")).toEqual(["c", "root"]);
  });

  it("handles a trace without spans", () => {
    const tl = buildTimeline([], { started_at: at(0), ended_at: null });
    expect(tl.roots).toEqual([]);
    expect(tl.totalMs).toBe(1);
  });
});
