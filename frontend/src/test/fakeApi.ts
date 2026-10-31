// Test-only network boundary: answers fetch() calls with responses shaped exactly like the backend's.
import { vi } from "vitest";

import type { Span, TraceDetail, TraceSummary } from "../api/types";

export type Route = (url: URL, init: RequestInit) => { status?: number; body?: unknown } | undefined;

export function installFakeApi(routes: Route[]): { calls: URL[] } {
  const calls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const url = new URL(input, "http://localhost:5173");
      calls.push(url);
      for (const route of routes) {
        const hit = route(url, init);
        if (hit) {
          const status = hit.status ?? 200;
          return new Response(status === 204 ? null : JSON.stringify(hit.body), { status, headers: { "Content-Type": "application/json" } });
        }
      }
      return new Response(JSON.stringify({ error: { code: "not_found", message: "Not found." } }), { status: 404 });
    }),
  );
  return { calls };
}

const T0 = Date.parse("2026-10-30T12:00:00.000Z");
const at = (ms: number) => new Date(T0 + ms).toISOString();

export const PROJECT = {
  id: "p1", name: "Support agent", slug: "support", description: "", agent_name: "support-bot", agent_version: null,
  environment: "development", settings: {}, is_demo: false, created_at: at(0), updated_at: at(0),
};

export function summary(id: string, extra: Partial<TraceSummary> = {}): TraceSummary {
  return {
    id, name: "support-request", status: "ok", started_at: at(0), ended_at: at(900), duration_ms: 900, span_count: 4,
    error_count: 0, total_tokens: 150, total_cost_usd: "0.004200", tags: ["sample-agent"], user_id: "cust-001",
    session_id: null, has_trace_record: true, ...extra,
  };
}

function span(id: string, parent: string | null, start: number, end: number, extra: Partial<Span>): Span {
  return {
    id, trace_id: "t1", parent_span_id: parent, name: id, kind: "custom", status: "ok", started_at: at(start),
    ended_at: at(end), duration_ms: end - start, input: null, output: null, metadata: {}, attributes: {}, error: null,
    model: null, prompt_tokens: null, completion_tokens: null, total_tokens: null, cost_usd: null, ...extra,
  };
}

export const DETAIL: TraceDetail = {
  ...summary("t1", { error_count: 1, span_count: 5 }),
  input: { question: "Where is my order A100?" },
  output: { answer: "Your order is shipped." },
  metadata: { agent_version: "sample-0.1" },
  spans: [
    span("agent-span", null, 0, 900, { name: "support-agent", kind: "agent" }),
    span("kb-span", "agent-span", 5, 20, {
      name: "kb-search", kind: "retrieval", input: { question: "Where is my order A100?" },
      attributes: { documents: [{ id: "kb-shipping", source: "kb/shipping.md", score: 0.4 }] },
    }),
    span("tool-span", "agent-span", 25, 60, {
      name: "order-lookup", kind: "tool", status: "error", attributes: { tool: { name: "order-lookup", arguments: { order_id: "A100" } } },
      error: { type: "LookupError", message: "order A100 not found", stacktrace: "Traceback (most recent call last): ..." },
    }),
    span("llm-span", "agent-span", 70, 880, {
      name: "compose-answer", kind: "llm", model: "sample-template-llm", prompt_tokens: 120, completion_tokens: 30,
      total_tokens: 150, cost_usd: "0.004200", attributes: { llm: { temperature: 0 } }, output: "Your order is shipped.",
    }),
    span("orphan-span", "never-received", 100, 120, { name: "late-callback" }),
  ],
};
