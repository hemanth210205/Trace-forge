import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CaseResult, Dataset, EvaluationRun } from "../api/types";
import { installFakeApi } from "../test/fakeApi";
import { DatasetPage, parseLoose } from "./DatasetPage";
import { RunPage } from "./EvaluationPages";

afterEach(() => vi.unstubAllGlobals());

const DATASET: Dataset = {
  id: "d1", project_id: "p1", name: "Support QA", description: "", metadata: {}, draft_case_count: 1,
  latest_version: { id: "v1", version: 1, case_count: 1, content_hash: "abc123", notes: "", created_at: "2026-10-30T12:00:00Z" },
  draft_changed_since_latest: false, created_at: "2026-10-30T12:00:00Z", updated_at: "2026-10-30T12:00:00Z",
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/datasets/:datasetId" element={<DatasetPage />} />
        <Route path="/projects/:projectId/evaluations/:runId" element={<RunPage />} />
        <Route path="*" element={<p>navigated</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("parseLoose", () => {
  it("parses JSON and keeps anything else as text", () => {
    expect(parseLoose('{"q": 1}')).toEqual({ q: 1 });
    expect(parseLoose("Where is my order?")).toBe("Where is my order?");
    expect(parseLoose("   ")).toBeNull();
  });
});

describe("DatasetPage", () => {
  it("warns what deleting removes and shows a failed delete instead of failing silently", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    installFakeApi([
      (url, init) => url.pathname === "/api/v1/projects/p1/datasets/d1" && init.method === "DELETE"
        ? { status: 500, body: { error: { code: "internal_error", message: "An internal error occurred." } } } : undefined,
      (url) => (url.pathname === "/api/v1/projects/p1/datasets/d1" ? { body: DATASET } : undefined),
      (url) => (url.pathname === "/api/v1/projects/p1/datasets/d1/cases" ? { body: [] } : undefined),
    ]);
    const user = userEvent.setup();
    renderAt("/projects/p1/datasets/d1");
    await user.click(await screen.findByRole("button", { name: "Delete dataset" }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("diagnoses, experiments and promotion decisions"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not delete the dataset: An internal error occurred.");
    expect(screen.queryByText("navigated")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete dataset" })).toBeEnabled();
  });

  it("adds a draft case with parsed fields", async () => {
    const bodies: unknown[] = [];
    installFakeApi([
      (url) => (url.pathname === "/api/v1/projects/p1/datasets/d1" ? { body: DATASET } : undefined),
      (url, init) => {
        if (url.pathname !== "/api/v1/projects/p1/datasets/d1/cases") return undefined;
        if (init.method === "POST") {
          bodies.push(JSON.parse(String(init.body)));
          return { status: 201, body: {} };
        }
        return { body: [] };
      },
    ]);
    const user = userEvent.setup();
    renderAt("/projects/p1/datasets/d1");
    await user.click(await screen.findByRole("button", { name: "Add case" }));
    const form = screen.getByRole("form", { name: "New case" });
    await user.type(within(form).getByLabelText(/^Key/), "shipping");
    await user.type(within(form).getByLabelText(/^Input/), '{{"question": "Where?"}');
    await user.type(within(form).getByLabelText(/^Expected output/), "Shipped.");
    await user.type(within(form).getByLabelText(/^Expected sources/), "kb/shipping.md{Enter}kb/orders.md");
    await user.click(within(form).getByRole("button", { name: "Add case" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ key: "shipping", input: { question: "Where?" }, expected_output: "Shipped.",
      expected_sources: ["kb/shipping.md", "kb/orders.md"], metadata: {} });
  });

  it("shows every import problem with its line number", async () => {
    installFakeApi([
      (url) => (url.pathname === "/api/v1/projects/p1/datasets/d1" ? { body: DATASET } : undefined),
      (url) => url.pathname.endsWith("/import") ? { status: 422, body: { error: { code: "invalid_import", message: "2 problem(s) found; nothing was imported.",
        details: [{ at: "line 2", message: "Invalid JSON: Expecting value." }, { at: "line 3", message: "input: input is required." }] } } } : undefined,
    ]);
    const user = userEvent.setup();
    renderAt("/projects/p1/datasets/d1?tab=import");
    await user.type(await screen.findByLabelText("Import content"), "x");
    await user.click(screen.getByRole("button", { name: "Validate and import" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("nothing was imported");
    expect(alert).toHaveTextContent("line 2: Invalid JSON");
    expect(alert).toHaveTextContent("line 3: input: input is required.");
  });

  it("disables publishing when the draft equals the latest version", async () => {
    installFakeApi([
      (url) => (url.pathname === "/api/v1/projects/p1/datasets/d1" ? { body: DATASET } : undefined),
      (url) => (url.pathname.endsWith("/versions") ? { body: [DATASET.latest_version] } : undefined),
    ]);
    renderAt("/projects/p1/datasets/d1?tab=versions");
    expect(await screen.findByRole("button", { name: "Publish v2" })).toBeDisabled();
    expect(screen.getByText("The draft is identical to the latest version.")).toBeInTheDocument();
  });

  it("creates a run on the chosen version with the configured evaluators", async () => {
    let created: Record<string, unknown> | null = null;
    installFakeApi([
      (url) => (url.pathname === "/api/v1/projects/p1/datasets/d1" ? { body: DATASET } : undefined),
      (url) => (url.pathname.endsWith("/versions") ? { body: [DATASET.latest_version] } : undefined),
      (url, init) => {
        if (url.pathname !== "/api/v1/projects/p1/eval-runs") return undefined;
        if (init.method === "POST") {
          created = JSON.parse(String(init.body));
          return { status: 201, body: { id: "r9" } };
        }
        return { body: [] };
      },
    ]);
    const user = userEvent.setup();
    renderAt("/projects/p1/datasets/d1?tab=evaluations");
    const form = await screen.findByRole("form", { name: "New evaluation run" });
    await user.type(within(form).getByLabelText("Normalized match: Output field"), "answer");
    await user.click(within(form).getByLabelText(/Required fields/));
    await user.type(within(form).getByLabelText("Required fields: Fields"), "answer, citations");
    await user.click(within(form).getByRole("button", { name: "Create run" }));
    await waitFor(() => expect(created).not.toBeNull());
    expect(created).toEqual({
      dataset_version_id: "v1", name: "Support QA evaluation",
      evaluators: [{ type: "normalized_match", field: "answer" }, { type: "source_match", min_recall: 1 }, { type: "required_fields", fields: ["answer", "citations"] }],
    });
  });
});

function run(extra: Partial<EvaluationRun>): EvaluationRun {
  return {
    id: "r1", project_id: "p1", dataset_id: "d1", dataset_name: "Support QA", dataset_version_id: "v1", dataset_version: 1,
    name: "nightly", status: "completed", evaluators: [], total_cases: 2, created_at: "2026-10-30T12:00:00Z",
    started_at: "2026-10-30T12:00:01Z", scoring_started_at: "2026-10-30T12:00:02Z", finished_at: "2026-10-30T12:00:03Z",
    failure_reason: null, received_cases: 2, pass_rate: 0.5,
    summary: { passed: 1, failed: 1, missing_cases: 0, mean_score: 0.75,
      evaluators: { source_match: { passed: 1, failed: 1, not_applicable: 0, pass_rate: 0.5 } } },
    ...extra,
  };
}

const FAILED_CASE: CaseResult = {
  case_id: "c2", key: "warranty", position: 2, input: { question: "Warranty?" }, expected_output: "No.", expected_sources: ["kb/warranty.md"],
  state: "failed", output: { answer: "Yes." }, execution_error: null, trace_id: "t2", latency_ms: 12.5, score: "0.5000",
  submitted_at: "2026-10-30T12:00:01Z",
  evaluations: [
    { evaluator: "normalized_match", type: "normalized_match", applicable: true, passed: false, score: 0, expected: "No.", actual: "Yes.",
      explanation: "Differs even after normalizing case, whitespace and punctuation.", supporting_refs: [{ trace_id: "t2" }] },
    { evaluator: "source_match", type: "source_match", applicable: true, passed: true, score: 1, expected: ["kb/warranty.md"], actual: ["kb/warranty.md"],
      explanation: "Retrieved 1/1 expected source(s).", supporting_refs: [{ trace_id: "t2", span_id: "span-7777aaaa", reason: "retrieval span" }] },
  ],
};
const PASSED_CASE: CaseResult = { ...FAILED_CASE, case_id: "c1", key: "shipping", state: "passed", score: "1.0000", trace_id: "t1", evaluations: [] };

describe("RunPage", () => {
  it("shows the pass rate, evaluator breakdown and failed-case evidence with trace links", async () => {
    installFakeApi([
      (url) => (url.pathname === "/api/v1/projects/p1/eval-runs/r1" ? { body: run({}) } : undefined),
      (url) => (url.pathname.endsWith("/results") ? { body: [PASSED_CASE, FAILED_CASE] } : undefined),
    ]);
    const user = userEvent.setup();
    renderAt("/projects/p1/evaluations/r1");
    expect(await screen.findByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText(/1 pass · 1 fail · 50.0%/)).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Failed/ }));
    const list = screen.getByRole("list", { name: "Case results" });
    expect(within(list).getAllByRole("button")).toHaveLength(1);
    await user.click(within(list).getByText("warranty"));
    const panel = screen.getByLabelText("Case warranty");
    expect(panel).toHaveTextContent("Differs even after normalizing");
    expect(within(panel).getByRole("link", { name: "Open trace →" })).toHaveAttribute("href", "/projects/p1/traces/t2");
    expect(within(panel).getByRole("link", { name: /retrieval span span-777/ })).toHaveAttribute("href", "/projects/p1/traces/t2?span=span-7777aaaa");
  });

  it("withholds the pass rate for an incomplete run and explains why", async () => {
    installFakeApi([
      (url) => url.pathname === "/api/v1/projects/p1/eval-runs/r1"
        ? { body: run({ status: "incomplete", pass_rate: null, received_cases: 1, summary: { passed: 1, failed: 0, missing_cases: 1 } }) } : undefined,
      (url) => (url.pathname.endsWith("/results") ? { body: [PASSED_CASE, { ...FAILED_CASE, state: "missing", trace_id: null, output: null, evaluations: [] }] } : undefined),
    ]);
    renderAt("/projects/p1/evaluations/r1");
    expect(await screen.findByText(/1 of 2 cases never received a result, so no pass rate is reported/)).toBeInTheDocument();
    expect(screen.queryByText("100%")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Missing/ })).toHaveTextContent("1");
  });

  it("explains how to execute a pending run and offers finish/abort", async () => {
    installFakeApi([
      (url) => url.pathname === "/api/v1/projects/p1/eval-runs/r1"
        ? { body: run({ status: "pending", pass_rate: null, received_cases: 0, started_at: null, scoring_started_at: null, finished_at: null, summary: {} }) } : undefined,
      (url) => (url.pathname.endsWith("/results") ? { body: [{ ...PASSED_CASE, state: "pending", evaluations: [] }] } : undefined),
    ]);
    renderAt("/projects/p1/evaluations/r1");
    expect(await screen.findByText(/traceforge.evaluate\("r1"/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Finish and score" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Abort" })).toBeInTheDocument();
  });
});
