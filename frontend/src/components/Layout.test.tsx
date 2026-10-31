import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { installFakeApi, PROJECT } from "../test/fakeApi";
import { Layout } from "./Layout";

afterEach(() => vi.unstubAllGlobals());

const USER = { id: "u1", email: "ada@example.com", display_name: "Ada", created_at: "2026-10-31T10:00:00Z" };

function renderAt(path: string, routes: Parameters<typeof installFakeApi>[0] = []) {
  installFakeApi([
    ...routes,
    (url) => (url.pathname === "/api/v1/auth/me" ? { body: { user: USER, csrf_token: "c" } } : undefined),
    (url) => (url.pathname === "/api/v1/projects" ? { body: [PROJECT] } : undefined),
    (url) => (url.pathname === "/api/v1/projects/p1" ? { body: PROJECT } : undefined),
  ]);
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<p>Projects page</p>} />
            <Route path="/projects/:projectId/traces" element={<p>Traces page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("Layout", () => {
  it("shows the project tabs and page for an owned project", async () => {
    renderAt("/projects/p1/traces");
    expect(await screen.findByText("Traces page")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Project" })).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: PROJECT.name, selected: true })).toBeInTheDocument();
  });

  it("replaces tabs and page with a clear state for an unknown or foreign project", async () => {
    renderAt("/projects/someone-elses/traces");
    expect(await screen.findByRole("alert")).toHaveTextContent("Project not found");
    expect(screen.getByText(/does not exist or belongs to another account/)).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Project" })).not.toBeInTheDocument();
    expect(screen.queryByText("Traces page")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to projects" })).toHaveAttribute("href", "/");
  });

  it("offers a retry when the project cannot be loaded for another reason", async () => {
    renderAt("/projects/p2/traces", [
      (url) => (url.pathname === "/api/v1/projects/p2" ? { status: 500, body: { error: { code: "internal_error", message: "An internal error occurred." } } } : undefined),
    ]);
    expect(await screen.findByRole("alert")).toHaveTextContent("An internal error occurred.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
