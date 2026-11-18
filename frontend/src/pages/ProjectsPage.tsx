import { useState, type FormEvent } from "react";
import { Link, useNavigate, useOutletContext } from "react-router-dom";
import { Activity, ArrowRight, FlaskConical, FolderGit2, Key, ListTree, Plus } from "lucide-react";

import { api, ApiError } from "../api/client";
import type { Project } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, Card, ErrorState, Field, SelectInput, Spinner, TextInput, cx } from "../components/ui";

const ENV_STYLE: Record<string, string> = {
  production: "border-accent-400/40 bg-accent-400/10 text-accent-300",
  staging: "border-pulse-500/40 bg-pulse-900/50 text-pulse-300",
};

const QUICKSTART = [
  { icon: FolderGit2, title: "Create a project", body: "One project per agent." },
  { icon: Key, title: "Generate an API key", body: "The SDK sends traces with it." },
  { icon: FlaskConical, title: "Send a trace", body: "Run your agent with the Python SDK." },
];
import { formatRelative } from "../lib/format";

export function ProjectsPage() {
  const projects = useApi((signal) => api<Project[]>("/projects", { signal }), []);
  const { reloadProjects } = useOutletContext<{ reloadProjects: () => void }>();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between border-b border-border pb-3">
        <div>
          <p className="font-mono text-2xs tracking-[0.14em] text-pulse-400">WORKSPACE</p>
          <h1 className="mt-0.5 text-2xl font-semibold tracking-tight text-slate-50">Projects</h1>
          <p className="text-xs text-slate-400 mt-1">
            Each project keeps its own traces, datasets, evaluations and API keys.
          </p>
        </div>
        {!creating && (
          <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-3.5 w-3.5" />
            <span>New project</span>
          </Button>
        )}
      </div>

      {creating && (
        <CreateProject
          onCancel={() => setCreating(false)}
          onCreated={(project) => {
            reloadProjects();
            navigate(`/projects/${project.id}/keys`);
          }}
        />
      )}

      {projects.loading && !projects.data && (
        <div className="py-12 text-center">
          <Spinner label="Loading projects" />
        </div>
      )}
      {projects.error && <ErrorState error={projects.error} onRetry={projects.reload} />}

      {projects.data && projects.data.length === 0 && !creating && (
        <div className="tf-glass relative rounded-3xl p-8 sm:p-10">
          <p className="font-mono text-2xs tracking-[0.14em] text-accent-400">GET STARTED</p>
          <h2 className="mt-2 text-xl font-semibold text-slate-50">No projects yet</h2>
          <p className="mt-1 max-w-lg text-sm text-slate-400">A project groups the traces of one agent. Three steps to your first trace:</p>
          <ol className="mt-6 grid gap-3 sm:grid-cols-3">
            {QUICKSTART.map(({ icon: Icon, title, body }, i) => (
              <li key={title} className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
                <div className="flex items-center justify-between">
                  <span className="grid h-9 w-9 place-items-center rounded-xl bg-accent-400/10 text-accent-400">
                    <Icon className="h-4 w-4" aria-hidden />
                  </span>
                  <span className="font-mono text-2xs text-slate-600">0{i + 1}</span>
                </div>
                <p className="mt-3 text-sm font-medium text-slate-100">{title}</p>
                <p className="mt-0.5 text-xs text-slate-500">{body}</p>
              </li>
            ))}
          </ol>
          <Button variant="primary" className="mt-6" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> Create a project
          </Button>
        </div>
      )}

      {projects.data && projects.data.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {projects.data.map((project) => (
            <Card key={project.id} className="group relative flex flex-col justify-between transition-all duration-300 hover:-translate-y-0.5 hover:border-accent-400/50">
              <div>
                <div className="flex items-start justify-between gap-2">
                  <span className={cx("inline-flex items-center rounded-full border px-2.5 py-0.5 font-mono text-2xs uppercase tracking-wider", ENV_STYLE[project.environment] ?? "border-border bg-surface-raised text-slate-300")}>
                    {project.environment}
                  </span>
                  <span className="text-2xs text-slate-500 font-mono">
                    {formatRelative(project.created_at)}
                  </span>
                </div>
                <h2 className="mt-4 font-mono text-base font-semibold text-slate-50 transition-colors group-hover:text-accent-300">
                  <Link to={`/projects/${project.id}/traces`} className="hover:underline">
                    {project.name}
                  </Link>
                </h2>
                <div className="mt-2 space-y-1 text-xs text-slate-400 font-mono">
                  <div className="flex items-center gap-1.5 text-slate-500">
                    <span>slug:</span>
                    <span className="text-slate-300">{project.slug}</span>
                  </div>
                  <div className="flex items-center gap-1.5 text-slate-500">
                    <span>agent:</span>
                    <span className="text-slate-300">
                      {project.agent_name}
                      {project.agent_version ? ` v${project.agent_version}` : ""}
                    </span>
                  </div>
                </div>
              </div>

              <div className="mt-5 flex items-center justify-between border-t border-border pt-3">
                <div className="flex items-center gap-2">
                  <Link
                    to={`/projects/${project.id}/overview`}
                    className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-100 transition-colors"
                  >
                    <Activity className="h-3.5 w-3.5" />
                    <span>Overview</span>
                  </Link>
                  <span className="text-slate-600">·</span>
                  <Link
                    to={`/projects/${project.id}/traces`}
                    className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-100 transition-colors"
                  >
                    <ListTree className="h-3.5 w-3.5" />
                    <span>Traces</span>
                  </Link>
                  <span className="text-slate-600">·</span>
                  <Link
                    to={`/projects/${project.id}/keys`}
                    className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-100 transition-colors"
                  >
                    <Key className="h-3.5 w-3.5" />
                    <span>API keys</span>
                  </Link>
                </div>
                <Link
                  to={`/projects/${project.id}/traces`}
                  aria-label={`Open ${project.name}`}
                  className="text-slate-500 transition-all group-hover:translate-x-0.5 group-hover:text-accent-400"
                >
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function CreateProject({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (project: Project) => void;
}) {
  const [name, setName] = useState("");
  const [agentName, setAgentName] = useState("");
  const [environment, setEnvironment] = useState("development");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(
        await api<Project>("/projects", {
          method: "POST",
          body: { name, agent_name: agentName, environment },
        }),
      );
    } catch (err) {
      setError(
        err instanceof ApiError && err.code === "slug_taken"
          ? "You already have a project with this name."
          : (err as Error).message,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_1fr_10rem_auto] sm:items-end">
        <Field label="Project name">
          <TextInput
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={100}
            autoFocus
            placeholder="e.g. Customer Support AI"
          />
        </Field>
        <Field label="Agent name">
          <TextInput
            value={agentName}
            onChange={(e) => setAgentName(e.target.value)}
            required
            maxLength={100}
            placeholder="e.g. support-bot"
          />
        </Field>
        <Field label="Environment">
          <SelectInput value={environment} onChange={(e) => setEnvironment(e.target.value)}>
            <option value="development">development</option>
            <option value="staging">staging</option>
            <option value="production">production</option>
          </SelectInput>
        </Field>
        <div className="flex gap-2">
          <Button variant="primary" type="submit" disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </Button>
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
        {error && <p role="alert" className="text-xs text-red-400 sm:col-span-4">{error}</p>}
      </form>
    </Card>
  );
}
