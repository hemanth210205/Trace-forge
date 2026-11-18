import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  ChevronRight,
  Database,
  FlaskConical,
  FolderGit2,
  GitCompare,
  Key,
  ListTree,
  LogOut,
  User,
} from "lucide-react";

import { api, ApiError } from "../api/client";
import type { Project } from "../api/types";
import { useApi } from "../api/useApi";
import { useAuth } from "../auth/AuthContext";
import { AuthorCredit } from "./AuthorCredit";
import { Backdrop } from "./Backdrop";
import { BrandMark } from "./BrandMark";
import { cx, EmptyState, ErrorState, SelectInput } from "./ui";

export function Layout() {
  const { user, logout } = useAuth();
  const { projectId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  const projects = useApi((signal) => api<Project[]>("/projects", { signal }), []);
  const current = useApi(
    (signal) => (projectId ? api<Project>(`/projects/${projectId}`, { signal }) : Promise.resolve(undefined)),
    [projectId],
  );

  const missing = current.error instanceof ApiError && current.error.status === 404;
  const switchable = [...(projects.data ?? [])];
  if (current.data && !switchable.some((p) => p.id === current.data!.id)) {
    switchable.unshift(current.data);
  }

  const currentProjectName = current.data?.name ?? (projectId ? "Project" : "All Projects");

  const navItem = (to: string, label: string, Icon: React.ComponentType<{ className?: string }>) => (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cx(
          "group flex shrink-0 items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-xs font-medium transition-colors duration-150",
          isActive
            ? "bg-accent-400/10 text-accent-300 font-semibold ring-1 ring-inset ring-accent-400/25"
            : "text-slate-400 hover:bg-surface-hover hover:text-slate-100",
        )
      }
    >
      <Icon className="h-4 w-4 shrink-0 opacity-80" />
      <span className="truncate">{label}</span>
    </NavLink>
  );

  // Derive current section for breadcrumb
  const pathParts = location.pathname.split("/").filter(Boolean);
  const currentSection = pathParts[2] ? pathParts[2].charAt(0).toUpperCase() + pathParts[2].slice(1) : "Overview";

  return (
    <div className="relative flex min-h-screen flex-col bg-app text-slate-100 font-sans antialiased lg:flex-row">
      <Backdrop intensity="subtle" />
      {/* Sidebar: fixed on the left from lg up; a compact block above the content on narrow screens */}
      <aside className="relative z-10 flex w-full flex-col border-b border-border bg-sidebar/85 backdrop-blur-md lg:fixed lg:inset-y-0 lg:left-0 lg:z-30 lg:w-60 lg:border-b-0 lg:border-r">
        {/* Brand Header */}
        <div className="flex h-12 items-center justify-between border-b border-border px-4">
          <Link to="/" className="flex items-center gap-2.5">
            <BrandMark />
            <span className="font-mono text-[13px] font-semibold uppercase tracking-[0.14em] text-slate-50">Traceforge</span>
          </Link>
          <span className="rounded-md border border-white/10 px-1.5 py-0.5 font-mono text-2xs text-accent-400">v0.1</span>
        </div>

        {/* Project Switcher Bar */}
        <div className="border-b border-border p-3">
          <label className="sr-only" htmlFor="project-switcher">
            Project
          </label>
          <div className="relative">
            <SelectInput
              id="project-switcher"
              className="w-full text-xs font-medium bg-surface border-border text-slate-200"
              value={projectId && !missing ? projectId : ""}
              onChange={(e) => navigate(e.target.value ? `/projects/${e.target.value}/overview` : "/")}
            >
              <option value="">Select Project / All</option>
              {switchable.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </SelectInput>
          </div>
        </div>

        {/* Navigation Menus */}
        <div className="min-w-0 px-3 py-2 lg:flex-1 lg:space-y-5 lg:overflow-y-auto lg:py-3">
          {projectId && !missing && (
            <div className="space-y-1">
              <div className="hidden px-3 pb-1.5 font-mono text-2xs uppercase tracking-[0.14em] text-slate-500 lg:block">
                Observability
              </div>
              <nav
                className="flex gap-1 overflow-x-auto lg:flex-col lg:gap-0 lg:space-y-0.5 lg:overflow-visible"
                aria-label="Project"
              >
                {navItem(`/projects/${projectId}/overview`, "Overview", Activity)}
                {navItem(`/projects/${projectId}/traces`, "Traces", ListTree)}
                {navItem(`/projects/${projectId}/evaluations`, "Evaluations", FlaskConical)}
                {navItem(`/projects/${projectId}/diagnoses`, "Diagnoses", AlertTriangle)}
                {navItem(`/projects/${projectId}/experiments`, "Experiments", GitCompare)}
                {navItem(`/projects/${projectId}/datasets`, "Datasets", Database)}
                {navItem(`/projects/${projectId}/keys`, "API Keys", Key)}
              </nav>
            </div>
          )}

          <div className="hidden space-y-1 lg:block">
            <div className="px-3 pb-1.5 font-mono text-2xs uppercase tracking-[0.14em] text-slate-500">
              Workspace
            </div>
            <nav className="space-y-0.5">
              <NavLink
                to="/"
                className={({ isActive }) =>
                  cx(
                    "group flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs font-medium transition-colors duration-150",
                    isActive
                      ? "bg-accent-400/10 text-accent-300 font-semibold ring-1 ring-inset ring-accent-400/25"
                      : "text-slate-400 hover:bg-surface hover:text-slate-200",
                  )
                }
              >
                <FolderGit2 className="h-4 w-4 shrink-0 opacity-80" />
                <span className="truncate">All Projects</span>
              </NavLink>
              {navItem("/docs", "Docs", BookOpen)}
              {navItem("/author", "Author", User)}
            </nav>
          </div>
        </div>

        {/* Signed-in user and sign out */}
        <div className="border-t border-border p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2.5">
              <div className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-surface-hover text-xs font-semibold text-slate-200">
                {user?.display_name?.slice(0, 1).toUpperCase() ?? "U"}
              </div>
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-slate-100">{user?.display_name}</p>
                <p className="truncate text-2xs text-slate-500">{user?.email}</p>
              </div>
            </div>
            <button
              type="button"
              className="rounded p-1.5 text-slate-500 transition-colors hover:bg-surface-hover hover:text-slate-100"
              title="Sign out"
              aria-label="Sign out"
              onClick={() => void logout().then(() => navigate("/login"))}
            >
              <LogOut className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </aside>

      {/* Main Viewport Content with Left Margin for Fixed Sidebar */}
      <div className="relative z-10 flex-1 flex flex-col min-w-0 lg:pl-60">
        {/* Sticky Top Header */}
        <header className="sticky top-0 z-20 flex h-12 items-center gap-3 border-b border-border bg-app/70 px-4 backdrop-blur-md lg:px-6">
          {/* Breadcrumb Path */}
          <div className="flex min-w-0 items-center gap-2 font-mono text-xs">
            <Link to="/" className="shrink-0 text-slate-400 hover:text-slate-200 transition-colors">
              Projects
            </Link>
            {projectId && (
              <>
                <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-600" />
                <span className="min-w-0 max-w-[180px] truncate font-medium text-slate-200">
                  {currentProjectName}
                </span>
                <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-600" />
                <span className="shrink-0 font-semibold text-slate-200">
                  {currentSection}
                </span>
              </>
            )}
          </div>
        </header>

        {/* Dynamic Route Body */}
        <main className="flex-1 p-4 sm:p-6 max-w-[1600px] w-full mx-auto">
          {missing ? (
            <div role="alert">
              <EmptyState
                title="Project not found"
                action={
                  <Link className="text-sm font-medium text-accent hover:underline" to="/">
                    Back to projects
                  </Link>
                }
              >
                This project does not exist or belongs to another account.
              </EmptyState>
            </div>
          ) : current.error ? (
            <ErrorState error={current.error} onRetry={current.reload} />
          ) : (
            <Outlet context={{ reloadProjects: projects.reload }} />
          )}
        </main>

        <footer className="border-t border-border bg-app/60 px-4 py-3 backdrop-blur lg:px-6">
          <AuthorCredit className="mx-auto max-w-[1600px]" />
        </footer>
      </div>
    </div>
  );
}
