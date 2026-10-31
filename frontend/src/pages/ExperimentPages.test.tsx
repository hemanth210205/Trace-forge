import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Experiment, ExperimentCase } from "../api/types";
import { installFakeApi } from "../test/fakeApi";
import { ExperimentPage, NewExperimentPage } from "./ExperimentPages";

afterEach(() => vi.unstubAllGlobals());

const arm = (state: "passed" | "failed", trace: string) => ({
  state, score: state === "passed" ? 1 : 0, output: { answer: "x" }, execution_error: null, evaluations: [], trace_id: trace,
  latency_ms: 10, tokens: 80, cost_usd: null,
});

const EXPERIMENT: Experiment = {
  id: "e1", project_id: "p1", name: "stemmed retriever", description: "", status: "completed", source_run_id: "r0", source_run_name: "baseline",
  dataset_id: "d1", dataset_name: "Support QA", dataset_version_id: "v1", dataset_version: 1,
  baseline: { run_id: "rb", config: { retriever: "keyword" }, run_status: "completed", received_cases: 2, total_cases: 2 },
  candidate: { run_id: "rc", config: { retriever: "stemmed" }, run_status: "completed", received_cases: 2, total_cases: 2 },
  config_diff: [{ path: "retriever", change: "changed", baseline: "keyword", candidate: "stemmed" }],
  hypothesis: { id: "h1", diagnosis_id: "dg1", case_key: "shipping-a100", category: "retrieval_failure", explanation: "kb/shipping.md not retrieved.",
    evidence_status: "supported", validation_status: "unvalidated", validated_by: null, recommended_experiment: "Try stemming." },
  hypothesis_validation: { eligible: true, reason: "Baseline reproduced the failure of shipping-a100 and the candidate fixed it." },
  created_at: "2026-10-31T10:00:00Z", started_at: "2026-10-31T10:00:01Z", deadline_at: "2026-10-31T11:00:01Z", finished_at: "2026-10-31T10:00:05Z",
  failure_reason: null, decisions: [],
  comparison: {
    arms: {
      baseline: { run_id: "rb", status: "completed", total_cases: 2, passed: 1, failed: 1, missing: 0, not_evaluated: 0, pass_rate: 0.5,
        evaluators: { source_match: { passed: 1, failed: 1, pass_rate: 0.5 } }, retrieval: { checked_cases: 2, mean_recall: 0.5 },
        latency_ms: { measured_cases: 2, mean: 10, median: 10 }, tokens: { measured_cases: 2, total: 160 }, cost_usd: { measured_cases: 0, total: null } },
      candidate: { run_id: "rc", status: "completed", total_cases: 2, passed: 2, failed: 0, missing: 0, not_evaluated: 0, pass_rate: 1,
        evaluators: { source_match: { passed: 2, failed: 0, pass_rate: 1 } }, retrieval: { checked_cases: 2, mean_recall: 1 },
        latency_ms: { measured_cases: 2, mean: 12, median: 12 }, tokens: { measured_cases: 2, total: 170 }, cost_usd: { measured_cases: 0, total: null } },
    },
    pass_rate_delta: 0.5,
    cases: { fixed: ["shipping-a100"], regressed: [], unchanged_pass: ["refund-policy"], unchanged_fail: [], incomplete: [] },
    evaluator_changes: [{ case: "shipping-a100", evaluator: "source_match", change: "fixed" }],
    measurements: {
      latency_ms: { paired_cases: 2, available: true, baseline_mean: 10, candidate_mean: 12, delta: 2 },
      tokens: { paired_cases: 2, available: true, baseline_mean: 80, candidate_mean: 85, delta: 5 },
      cost_usd: { paired_cases: 0, available: false, baseline_mean: null, candidate_mean: null, delta: null },
    },
    suggested_outcome: "candidate_improves",
    suggested_outcome_reason: "1 case(s) fixed with no regressions. This is a computed suggestion; promotion requires a human decision.",
  },
};

const CASES: ExperimentCase[] = [
  { case_key: "shipping-a100", position: 1, input: {}, expected_output: "x", change: "fixed", evaluator_changes: [{ evaluator: "source_match", change: "fixed" }],
    baseline: arm("failed", "tb1"), candidate: arm("passed", "tc1") },
  { case_key: "refund-policy", position: 2, input: {}, expected_output: "y", change: "unchanged_pass", evaluator_changes: [],
    baseline: arm("passed", "tb2"), candidate: arm("passed", "tc2") },
];

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/experiments/new" element={<NewExperimentPage />} />
        <Route path="/projects/:projectId/experiments/:experimentId" element={<ExperimentPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ExperimentPage", () => {
  it("shows the diff, measured comparison, per-case trace links and the hypothesis as context", async () => {
    const posts: { path: string; body: unknown }[] = [];
    installFakeApi([
      (url, init) => {
        if (init.method === "POST") {
          posts.push({ path: url.pathname, body: init.body ? JSON.parse(String(init.body)) : null });
          return { status: 201, body: EXPERIMENT };
        }
        if (url.pathname === "/api/v1/projects/p1/experiments/e1") return { body: EXPERIMENT };
        if (url.pathname === "/api/v1/projects/p1/experiments/e1/cases") return { body: CASES };
        return undefined;
      },
    ]);
    const user = userEvent.setup();
    renderAt("/projects/p1/experiments/e1");
    const diff = await screen.findByRole("region", { name: "What differs" });
    expect(diff).toHaveTextContent("retriever");
    expect(diff).toHaveTextContent('"keyword"');
    expect(diff).toHaveTextContent('"stemmed"');

    const metrics = await screen.findByRole("table", { name: "Metrics" });
    expect(metrics).toHaveTextContent("+50.0 pts");
    expect(within(metrics).getByText("Cost (mean)").closest("tr")).toHaveTextContent("not measured");
    expect(within(metrics).getByText("Latency (mean)").closest("tr")).toHaveTextContent("2 paired case(s)");
    expect(screen.getByText("Candidate improves")).toBeInTheDocument();
    expect(screen.getByText(/computed suggestion; promotion requires a human decision/)).toBeInTheDocument();

    const perCase = await screen.findByRole("table", { name: "Per-case comparison" });
    expect(within(perCase).getAllByRole("link", { name: "baseline trace" })[0]).toHaveAttribute("href", "/projects/p1/traces/tb1");
    expect(within(perCase).getAllByRole("link", { name: "candidate trace" })[0]).toHaveAttribute("href", "/projects/p1/traces/tc1");
    expect(perCase).toHaveTextContent("Fixed");

    expect(screen.getByText(/Diagnosis recommendation \(not evidence\)/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Mark hypothesis validated" }));
    await waitFor(() => expect(posts.map((p) => p.path)).toContain("/api/v1/projects/p1/experiments/e1/validate-hypothesis"));

    const decision = screen.getByRole("region", { name: "Promotion decision" });
    expect(decision).toHaveTextContent("does not change any agent, configuration or deployment");
    const approve = within(decision).getByRole("button", { name: "Approve promotion" });
    expect(approve).toBeDisabled();
    await user.type(within(decision).getByLabelText(/Rationale/), "Fixes retrieval, no regressions.");
    await user.click(approve);
    await waitFor(() => expect(posts.at(-1)).toEqual({ path: "/api/v1/projects/p1/experiments/e1/decisions",
      body: { decision: "approve", rationale: "Fixes retrieval, no regressions." } }));
  });

  it("shows execution instructions and no decision form while running", async () => {
    const running = { ...EXPERIMENT, status: "running" as const, comparison: {}, finished_at: null,
      baseline: { ...EXPERIMENT.baseline, run_status: "pending" as const, received_cases: 0 }, candidate: { ...EXPERIMENT.candidate, run_status: "pending" as const, received_cases: 0 } };
    installFakeApi([(url) => url.pathname === "/api/v1/projects/p1/experiments/e1" ? { body: running }
      : url.pathname.endsWith("/cases") ? { body: [] } : undefined]);
    renderAt("/projects/p1/experiments/e1");
    expect(await screen.findByText(/traceforge.run_experiment\("e1", task\)/)).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Metrics" })).not.toBeInTheDocument();
    expect(screen.getByText("Decisions can be recorded once the experiment has completed.")).toBeInTheDocument();
  });
});

describe("NewExperimentPage", () => {
  it("creates an experiment from a completed run with parsed configurations", async () => {
    let body: Record<string, unknown> | null = null;
    installFakeApi([
      (url, init) => {
        if (url.pathname === "/api/v1/projects/p1/eval-runs/r0") return { body: { id: "r0", name: "baseline", status: "completed", dataset_name: "Support QA", dataset_version: 1 } };
        if (url.pathname === "/api/v1/projects/p1/experiments" && init.method === "POST") {
          body = JSON.parse(String(init.body));
          return { status: 201, body: EXPERIMENT };
        }
        if (url.pathname.startsWith("/api/v1/projects/p1/experiments/e1")) return { body: url.pathname.endsWith("/cases") ? CASES : EXPERIMENT };
        return undefined;
      },
    ]);
    const user = userEvent.setup();
    renderAt("/projects/p1/experiments/new?source_run=r0&hypothesis=h1");
    const form = await screen.findByRole("form", { name: "New experiment" });
    await user.type(within(form).getByLabelText("Name"), "stemmed retriever");
    const candidate = within(form).getByLabelText("Candidate configuration (JSON object)");
    await user.clear(candidate);
    await user.type(candidate, "not json");
    await user.click(within(form).getByRole("button", { name: "Create experiment" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Candidate configuration is not valid JSON.");
    await user.clear(candidate);
    await user.type(candidate, '{{"retriever": "stemmed"}');
    await user.click(within(form).getByRole("button", { name: "Create experiment" }));
    await waitFor(() => expect(body).toEqual({ source_run_id: "r0", name: "stemmed retriever", description: "",
      baseline_config: { retriever: "keyword" }, candidate_config: { retriever: "stemmed" }, hypothesis_id: "h1" }));
  });
});
