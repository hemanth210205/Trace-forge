import { Link, NavLink, Outlet, useNavigate, useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { Project } from "../api/types";
import { useApi } from "../api/useApi";
import { useAuth } from "../auth/AuthContext";
import { cx, EmptyState, ErrorState, SelectInput } from "./ui";

export function Layout() {
  const { user, logout } = useAuth();
  const { projectId } = useParams();
  const navigate = useNavigate();
  const projects = useApi((signal) => api<Project[]>("/projects", { signal }), []);
  // The current project is loaded on its own: an unknown or foreign id must not show project tabs and pages.
  const current = useApi(
    (signal) => (projectId ? api<Project>(`/projects/${projectId}`, { signal }) : Promise.resolve(undefined)),
    [projectId],
  );
  const missing = current.error instanceof ApiError && current.error.status === 404;
  const switchable = [...(projects.data ?? [])];
  if (current.data && !switchable.some((p) => p.id === current.data!.id)) switchable.unshift(current.data);

  const tab = (to: string, label: string) => (
    <NavLink
      to={to}
      end={false}
      className={({ isActive }) =>
        cx(
          "whitespace-nowrap border-b-2 px-1 pb-2 pt-2.5 text-sm",
          isActive ? "border-ink-900 font-semibold text-ink-900" : "border-transparent text-ink-500 hover:text-ink-900",
        )
      }
    >
      {label}
    </NavLink>
  );

  return (
    <div className="min-h-screen bg-ink-50 text-ink-900">
      <header className="sticky top-0 z-20 border-b border-ink-200 bg-white">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-4 gap-y-2 px-4 pt-2 sm:px-6">
          <Link to="/" className="flex items-center gap-2 pb-2 font-semibold tracking-tight">
            <span className="grid h-5 w-5 place-items-center bg-ink-900 font-mono text-2xs text-white">tf</span>
            Traceforge
          </Link>
          {switchable.length > 0 && (
            <div className="pb-2">
              <label className="sr-only" htmlFor="project-switcher">
                Project
              </label>
              <SelectInput
                id="project-switcher"
                className="max-w-[16rem]"
                value={projectId && !missing ? projectId : ""}
                onChange={(e) => navigate(e.target.value ? `/projects/${e.target.value}/traces` : "/")}
              >
                <option value="">All projects</option>
                {switchable.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </SelectInput>
            </div>
          )}
          {projectId && !missing && (
            <nav className="-mx-1 flex max-w-full items-end gap-4 self-end overflow-x-auto px-1" aria-label="Project">
              {tab(`/projects/${projectId}/traces`, "Traces")}
              {tab(`/projects/${projectId}/datasets`, "Datasets")}
              {tab(`/projects/${projectId}/evaluations`, "Evaluations")}
              {tab(`/projects/${projectId}/diagnoses`, "Diagnoses")}
              {tab(`/projects/${projectId}/experiments`, "Experiments")}
              {tab(`/projects/${projectId}/keys`, "API keys")}
            </nav>
          )}
          <div className="ml-auto flex items-center gap-3 pb-2 text-sm">
            <span className="hidden text-ink-500 sm:inline" title={user?.email}>
              {user?.display_name}
            </span>
            <button
              type="button"
              className="text-ink-500 hover:text-ink-900"
              onClick={() => void logout().then(() => navigate("/login"))}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6">
        {missing ? (
          <div role="alert">
            <EmptyState title="Project not found" action={<Link className="text-sm font-medium underline" to="/">Back to projects</Link>}>
              This project does not exist or belongs to another account.
            </EmptyState>
          </div>
        ) : current.error ? (
          <ErrorState error={current.error} onRetry={current.reload} />
        ) : (
          <Outlet context={{ reloadProjects: projects.reload }} />
        )}
      </main>
    </div>
  );
}
