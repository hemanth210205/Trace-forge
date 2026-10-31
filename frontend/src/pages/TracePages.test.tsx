import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DETAIL, PROJECT, installFakeApi, summary } from "../test/fakeApi";
import { TraceDetailPage } from "./TraceDetailPage";
import { TracesPage } from "./TracesPage";

afterEach(() => vi.unstubAllGlobals());

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/traces" element={<TracesPage />} />
        <Route path="/projects/:projectId/traces/:traceId" element={<TraceDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const projectRoute = (url: URL) => (url.pathname === "/api/v1/projects/p1" ? { body: PROJECT } : undefined);

describe("TracesPage", () => {
  it("lists traces from the API with status, duration, spans and links", async () => {
    installFakeApi([
      projectRoute,
      (url) => url.pathname === "/api/v1/projects/p1/traces" ? { body: { items: [summary("t1"), summary("t2", { status: "error", name: "refund", error_count: 2 })], next_cursor: null } } : undefined,
    ]);
    renderAt("/projects/p1/traces");
    const row = (await screen.findByRole("link", { name: "refund" })).closest("tr")!;
    expect(row).toHaveTextContent("Error");
    expect(within(row).getByText("2")).toHaveClass("text-red-700");
    expect(screen.getByRole("link", { name: "refund" })).toHaveAttribute("href", "/projects/p1/traces/t2");
    expect(screen.getAllByText("900 ms")).toHaveLength(2);
    expect(screen.getByText("2 traces · end of results")).toBeInTheDocument();
  });

  it("sends filters to the API and paginates with the cursor", async () => {
    const { calls } = installFakeApi([
      projectRoute,
      (url) => {
        if (url.pathname !== "/api/v1/projects/p1/traces") return undefined;
        if (url.searchParams.get("cursor") === "c1") return { body: { items: [summary("t3", { name: "older" })], next_cursor: null } };
        return { body: { items: [summary("t1")], next_cursor: "c1" } };
      },
    ]);
    const user = userEvent.setup();
    renderAt("/projects/p1/traces");
    await screen.findByRole("link", { name: "support-request" });

    await user.selectOptions(screen.getByLabelText("Status"), "error");
    await user.type(screen.getByLabelText("Search traces"), "refund{Enter}");
    await user.click(screen.getByLabelText("Spans with errors"));
    await waitFor(() => {
      const last = calls.filter((u) => u.pathname.endsWith("/traces")).at(-1)!;
      expect(last.searchParams.get("status")).toBe("error");
      expect(last.searchParams.get("q")).toBe("refund");
      expect(last.searchParams.get("has_errors")).toBe("true");
    });

    await user.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("link", { name: "older" })).toBeInTheDocument();
    expect(calls.some((u) => u.searchParams.get("cursor") === "c1")).toBe(true);
  });

  it("shows an onboarding empty state and an error state", async () => {
    installFakeApi([projectRoute, (url) => (url.pathname.endsWith("/traces") ? { body: { items: [], next_cursor: null } } : undefined)]);
    renderAt("/projects/p1/traces");
    expect(await screen.findByText("No traces yet")).toBeInTheDocument();

    vi.unstubAllGlobals();
    installFakeApi([projectRoute, (url) => (url.pathname.endsWith("/traces") ? { status: 500, body: { error: { code: "internal_error", message: "An internal error occurred." } } } : undefined)]);
    renderAt("/projects/p1/traces");
    expect(await screen.findByRole("alert")).toHaveTextContent("An internal error occurred.");
  });
});

describe("TraceDetailPage", () => {
  const detailRoute = (url: URL) => (url.pathname === "/api/v1/projects/p1/traces/t1" ? { body: DETAIL } : undefined);

  it("renders the span hierarchy, an orphan marker and the trace overview", async () => {
    installFakeApi([detailRoute]);
    renderAt("/projects/p1/traces/t1");
    const tree = await screen.findByRole("tree", { name: "Span timeline" });
    const items = within(tree).getAllByRole("treeitem");
    expect(items.map((i) => i.getAttribute("aria-level"))).toEqual(["1", "2", "2", "2", "1"]);
    expect(within(items[4]).getByText("orphan")).toBeInTheDocument();
    expect(screen.getByText(/1 with a parent that was not received/)).toBeInTheDocument();
    expect(screen.getByLabelText("Trace details")).toHaveTextContent("Where is my order A100?");
    expect(screen.getByText("$0.0042")).toBeInTheDocument();
  });

  it("shows LLM, tool, error and retrieval details only where present", async () => {
    installFakeApi([detailRoute]);
    const user = userEvent.setup();
    renderAt("/projects/p1/traces/t1");
    const tree = await screen.findByRole("tree");

    await user.click(within(tree).getByText("compose-answer"));
    let panel = screen.getByLabelText("Span compose-answer");
    expect(panel).toHaveTextContent("Model call");
    expect(panel).toHaveTextContent("sample-template-llm");
    expect(panel).toHaveTextContent("120 in · 30 out · 150 total");

    await user.click(within(tree).getByText("order-lookup"));
    panel = screen.getByLabelText("Span order-lookup");
    expect(panel).toHaveTextContent("LookupError");
    expect(panel).toHaveTextContent("order A100 not found");
    expect(panel).toHaveTextContent("Tool call");
    expect(panel).not.toHaveTextContent("Model call");

    await user.click(within(tree).getByText("kb-search"));
    panel = screen.getByLabelText("Span kb-search");
    expect(panel).toHaveTextContent("Retrieved documents (1)");
    expect(panel).toHaveTextContent("kb/shipping.md");
    expect(panel).toHaveTextContent("score 0.400");

    await user.click(within(tree).getByText("late-callback"));
    expect(screen.getByLabelText("Span late-callback")).toHaveTextContent("not received (never-r");
  });

  it("collapses a branch", async () => {
    installFakeApi([detailRoute]);
    const user = userEvent.setup();
    renderAt("/projects/p1/traces/t1");
    await user.click(await screen.findByRole("button", { name: "Collapse support-agent" }));
    expect(within(screen.getByRole("tree")).getAllByRole("treeitem")).toHaveLength(2);
    expect(screen.getByText("+3")).toBeInTheDocument();
  });

  it("shows not-found for a trace outside the user's projects", async () => {
    installFakeApi([]);
    renderAt("/projects/p1/traces/other");
    expect(await screen.findByText("Trace not found")).toBeInTheDocument();
  });
});
