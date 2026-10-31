import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DiagnosisDetail } from "../api/types";
import { installFakeApi } from "../test/fakeApi";
import { DiagnoseButton, DiagnosisPage } from "./DiagnosisPages";

afterEach(() => vi.unstubAllGlobals());

const DIAGNOSIS: DiagnosisDetail = {
  id: "dg1", project_id: "p1", run_id: "r1", run_name: "baseline", retry_of: null, status: "completed", provider: "mock", model: null,
  created_at: "2026-10-31T10:00:00Z", started_at: "2026-10-31T10:00:00Z", finished_at: "2026-10-31T10:00:01Z", error: null,
  summary: "Analyzed 2 failed case(s); evidence supports context selection failure (1). No cause is experimentally validated yet.",
  stats: { provider_calls: 2, provider_retries: 0, duration_ms: 42 }, category_counts: { context_selection_failure: 1 },
  cases: [
    {
      id: "c1", result_id: "res1", position: 1, case_key: "warranty-accidental-damage", trace_id: "t1", summary: "",
      missing_evidence: [],
      facts: [
        { id: "F1", kind: "evaluation", statement: "Evaluator normalized_match failed.", trace_id: null, span_id: null },
        { id: "F2", kind: "retrieval", statement: "Expected source kb/warranty.md was retrieved (rank 2 of 2).", trace_id: "t1", span_id: "span-retrieval-1" },
        { id: "F3", kind: "comparison", statement: "92% of the answer's content words occur in document kb/returns.md.", trace_id: "t1", span_id: "span-retrieval-1" },
      ],
      hypotheses: [
        { id: "h1", case_id: "c1", rank: 1, category: "context_selection_failure", explanation: "The answer was built from kb/returns.md.",
          supporting_facts: ["F1", "F2", "F3"], contradicting_facts: [], evidence_trace_ids: ["t1"], evidence_span_ids: ["span-retrieval-1"],
          evidence_status: "supported", confidence: "0.850", uncertainty: "Document attribution is lexical.", missing_evidence: [],
          recommended_experiment: "Re-run with re-ranking towards the expected source.", validation_status: "unvalidated", validated_by: null },
        { id: "h2", case_id: "c1", rank: 2, category: "grounding_failure", explanation: "The answer may not be supported.",
          supporting_facts: [], contradicting_facts: ["F3"], evidence_trace_ids: ["t1"], evidence_span_ids: ["span-retrieval-1"],
          evidence_status: "contradicted", confidence: "0.150", uncertainty: "The answer closely matches a retrieved document.",
          missing_evidence: [], recommended_experiment: "", validation_status: "unvalidated", validated_by: null },
      ],
    },
    {
      id: "c2", result_id: "res2", position: 2, case_key: "untraced-case", trace_id: null, summary: "",
      missing_evidence: ["A trace of this case's execution (submit trace_id with the result)."],
      facts: [{ id: "F1", kind: "evidence_gap", statement: "No trace was submitted with this result.", trace_id: null, span_id: null }],
      hypotheses: [
        { id: "h3", case_id: "c2", rank: 1, category: "insufficient_evidence", explanation: "No usable trace is linked.",
          supporting_facts: ["F1"], contradicting_facts: [], evidence_trace_ids: [], evidence_span_ids: [], evidence_status: "inconclusive",
          confidence: null, uncertainty: "This states a gap in the evidence, not a cause.", missing_evidence: [], recommended_experiment: "Re-run with tracing.",
          validation_status: "unvalidated", validated_by: null },
      ],
    },
  ],
};

function renderAt(path: string, element = <DiagnosisPage />) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/diagnoses/:diagnosisId" element={element} />
        <Route path="/projects/:projectId/evaluations/:runId" element={<DiagnoseButton projectId="p1" runId="r1" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DiagnosisPage", () => {
  it("separates facts, hypotheses and validated causes and links evidence to spans", async () => {
    installFakeApi([(url) => (url.pathname === "/api/v1/projects/p1/diagnoses/dg1" ? { body: DIAGNOSIS } : undefined)]);
    renderAt("/projects/p1/diagnoses/dg1");
    const warranty = await screen.findByRole("region", { name: "Diagnosis of case warranty-accidental-damage" });

    const facts = within(warranty).getByRole("list", { name: "Observed facts" });
    expect(within(facts).getAllByRole("listitem")).toHaveLength(3);
    expect(within(facts).getAllByRole("link", { name: "span span-ret" })[0]).toHaveAttribute("href", "/projects/p1/traces/t1?span=span-retrieval-1");

    const supported = within(warranty).getByRole("article", { name: "Hypothesis Context selection failure" });
    expect(supported).toHaveTextContent("Evidence supports");
    expect(supported).toHaveTextContent("Unvalidated hypothesis");
    expect(supported).toHaveTextContent("confidence 0.85");
    expect(within(supported).getByRole("link", { name: "span-ret" })).toHaveAttribute("href", "/projects/p1/traces/t1?span=span-retrieval-1");
    expect(within(supported).getByRole("link", { name: "F3" })).toHaveAttribute("href", "#fact-F3");

    const contradicted = within(warranty).getByRole("article", { name: "Hypothesis Grounding failure" });
    expect(contradicted).toHaveTextContent("Evidence contradicts");
    expect(contradicted).toHaveTextContent("Contradicted by");
    expect(within(warranty).getByText(/None. No cause for this case has been confirmed by an experiment./)).toBeInTheDocument();
    expect(screen.getByText(/No cause is experimentally validated yet/)).toBeInTheDocument();
  });

  it("shows insufficient evidence without inventing links or confidence", async () => {
    installFakeApi([(url) => (url.pathname === "/api/v1/projects/p1/diagnoses/dg1" ? { body: DIAGNOSIS } : undefined)]);
    renderAt("/projects/p1/diagnoses/dg1");
    const untraced = await screen.findByRole("region", { name: "Diagnosis of case untraced-case" });
    expect(within(untraced).getByText("no trace linked")).toBeInTheDocument();
    const hypothesis = within(untraced).getByRole("article", { name: "Hypothesis Insufficient evidence" });
    expect(hypothesis).toHaveTextContent("Inconclusive");
    expect(hypothesis).toHaveTextContent("confidence n/a");
    expect(within(untraced).queryAllByRole("link").filter((l) => l.getAttribute("href")?.includes("/traces/"))).toHaveLength(0);
    expect(untraced).toHaveTextContent("A trace of this case's execution");
  });

  it("shows a failed diagnosis with its error", async () => {
    installFakeApi([(url) => (url.pathname === "/api/v1/projects/p1/diagnoses/dg1"
      ? { body: { ...DIAGNOSIS, status: "failed", error: "Provider anthropic failed on case k: timed out.", summary: "", cases: [] } } : undefined)]);
    renderAt("/projects/p1/diagnoses/dg1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Diagnosis failed: Provider anthropic failed on case k: timed out.");
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
  });

  it("shows why a re-run could not start instead of failing silently", async () => {
    let posts = 0;
    installFakeApi([(url, init) => {
      if (url.pathname === "/api/v1/projects/p1/diagnoses/dg1/retry" && init.method === "POST") {
        posts += 1;
        return { status: 503, body: { error: { code: "diagnosis_provider_unavailable", message: "ANTHROPIC_API_KEY is not set." } } };
      }
      return url.pathname === "/api/v1/projects/p1/diagnoses/dg1"
        ? { body: { ...DIAGNOSIS, status: "failed", error: "Interrupted: the diagnosis stopped without finishing (server restart or crash). Retry to run it again.", summary: "", cases: [] } }
        : undefined;
    }]);
    const user = userEvent.setup();
    renderAt("/projects/p1/diagnoses/dg1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Interrupted: the diagnosis stopped without finishing");
    await user.click(screen.getByRole("button", { name: "Run again" }));
    expect((await screen.findByText("ANTHROPIC_API_KEY is not set.")).closest("[role=alert]")).toHaveTextContent("diagnosis_provider_unavailable");
    expect(posts).toBe(1);
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
  });
});

describe("DiagnoseButton", () => {
  it("starts a diagnosis and opens it", async () => {
    let posted = false;
    installFakeApi([
      (url, init) => {
        if (url.pathname === "/api/v1/projects/p1/eval-runs/r1/diagnoses" && init.method === "POST") {
          posted = true;
          return { status: 202, body: { ...DIAGNOSIS, status: "pending", cases: undefined } };
        }
        return url.pathname === "/api/v1/projects/p1/diagnoses/dg1" ? { body: DIAGNOSIS } : undefined;
      },
    ]);
    const user = userEvent.setup();
    renderAt("/projects/p1/evaluations/r1");
    await user.click(screen.getByRole("button", { name: "Diagnose failures" }));
    expect(await screen.findByText("Diagnosis of baseline")).toBeInTheDocument();
    expect(posted).toBe(true);
  });
});
