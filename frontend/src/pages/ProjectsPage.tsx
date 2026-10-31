import { useState, type FormEvent } from "react";
import { Link, useNavigate, useOutletContext } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { Project } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, EmptyState, ErrorState, Field, SelectInput, Spinner, TextInput } from "../components/ui";
import { formatRelative } from "../lib/format";

export function ProjectsPage() {
  const projects = useApi((signal) => api<Project[]>("/projects", { signal }), []);
  const { reloadProjects } = useOutletContext<{ reloadProjects: () => void }>();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Projects</h1>
        {!creating && (
          <Button variant="primary" onClick={() => setCreating(true)}>
            New project
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
      {projects.loading && !projects.data && <Spinner label="Loading projects" />}
      {projects.error && <ErrorState error={projects.error} onRetry={projects.reload} />}
      {projects.data && projects.data.length === 0 && !creating && (
        <EmptyState title="No projects yet" action={<Button variant="primary" onClick={() => setCreating(true)}>Create a project</Button>}>
          A project groups the traces of one agent. Create one, generate an API key, and point the Python SDK at it.
        </EmptyState>
      )}
      {projects.data && projects.data.length > 0 && (
        <ul className="divide-y divide-ink-200 border border-ink-200 bg-white">
          {projects.data.map((project) => (
            <li key={project.id} className="flex flex-wrap items-center gap-x-6 gap-y-1 px-4 py-3">
              <div className="min-w-0 flex-1">
                <Link to={`/projects/${project.id}/traces`} className="font-medium hover:underline">
                  {project.name}
                </Link>
                <p className="truncate text-xs text-ink-500">
                  <span className="font-mono">{project.slug}</span> · agent {project.agent_name}
                  {project.agent_version ? ` ${project.agent_version}` : ""} · {project.environment}
                </p>
              </div>
              <span className="text-xs text-ink-500">created {formatRelative(project.created_at)}</span>
              <div className="flex gap-2">
                <Link className="text-sm text-ink-700 hover:underline" to={`/projects/${project.id}/traces`}>
                  Traces
                </Link>
                <Link className="text-sm text-ink-700 hover:underline" to={`/projects/${project.id}/keys`}>
                  API keys
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CreateProject({ onCancel, onCreated }: { onCancel: () => void; onCreated: (project: Project) => void }) {
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
      onCreated(await api<Project>("/projects", { method: "POST", body: { name, agent_name: agentName, environment } }));
    } catch (err) {
      setError(err instanceof ApiError && err.code === "slug_taken" ? "You already have a project with this name." : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mb-4 grid gap-3 border border-ink-200 bg-white p-4 sm:grid-cols-[1fr_1fr_10rem_auto] sm:items-end">
      <Field label="Project name">
        <TextInput value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} autoFocus />
      </Field>
      <Field label="Agent name">
        <TextInput value={agentName} onChange={(e) => setAgentName(e.target.value)} required maxLength={100} placeholder="support-bot" />
      </Field>
      <Field label="Environment">
        <SelectInput value={environment} onChange={(e) => setEnvironment(e.target.value)} className="w-full">
          <option value="development">development</option>
          <option value="staging">staging</option>
          <option value="production">production</option>
        </SelectInput>
      </Field>
      <div className="flex gap-2">
        <Button variant="primary" type="submit" disabled={busy}>
          Create
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      {error && <p role="alert" className="text-sm text-red-700 sm:col-span-4">{error}</p>}
    </form>
  );
}
