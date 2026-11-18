import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Database, Plus } from "lucide-react";

import { api, ApiError } from "../api/client";
import type { Dataset } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, Card, EmptyState, ErrorState, Field, Spinner, TextInput } from "../components/ui";
import { formatRelative } from "../lib/format";

export function DatasetsPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const datasets = useApi((signal) => api<Dataset[]>(`/projects/${projectId}/datasets`, { signal }), [projectId]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const dataset = await api<Dataset>(`/projects/${projectId}/datasets`, { method: "POST", body: { name, description } });
      navigate(`/projects/${projectId}/datasets/${dataset.id}`);
    } catch (err) {
      setError(err instanceof ApiError && err.code === "dataset_name_taken" ? "A dataset with this name already exists." : (err as Error).message);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between border-b border-border pb-3">
        <div>
          <div className="flex items-center gap-2">
            <Database className="h-5 w-5 text-slate-500" />
            <h1 className="text-xl font-semibold tracking-tight text-slate-100">Datasets</h1>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Test cases for your agent. Publish a version to evaluate against it.
          </p>
        </div>
        {!creating && (
          <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-3.5 w-3.5" />
            <span>New Dataset</span>
          </Button>
        )}
      </div>

      {creating && (
        <Card>
          <form onSubmit={create} className="grid gap-3 sm:grid-cols-[1fr_2fr_auto] sm:items-end">
            <Field label="Dataset Name">
              <TextInput value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} autoFocus placeholder="e.g. Support QA Suite" />
            </Field>
            <Field label="Description">
              <TextInput value={description} onChange={(e) => setDescription(e.target.value)} maxLength={5000} placeholder="Benchmark purpose or regression cases" />
            </Field>
            <div className="flex gap-2">
              <Button variant="primary" type="submit">Create</Button>
              <Button type="button" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
            </div>
            {error && <p role="alert" className="text-xs text-red-400 sm:col-span-3">{error}</p>}
          </form>
        </Card>
      )}

      {datasets.loading && !datasets.data && (
        <div className="py-12 text-center">
          <Spinner label="Loading datasets" />
        </div>
      )}

      {datasets.error && <ErrorState error={datasets.error} onRetry={datasets.reload} />}

      {datasets.data && datasets.data.length === 0 && !creating && (
        <EmptyState
          title="No datasets yet"
          action={<Button variant="primary" onClick={() => setCreating(true)}>Create a dataset</Button>}
          icon={<Database className="h-8 w-8 text-slate-600" />}
        >
          A dataset holds test cases: an input for your agent, the expected output, and the sources it should retrieve.
          Publish a version, then run an evaluation against it.
        </EmptyState>
      )}

      {datasets.data && datasets.data.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border bg-surface shadow-sm">
          <table className="w-full text-sm border-collapse">
            <thead className="border-b border-border bg-surface-raised text-left text-xs font-medium text-slate-400">
              <tr>
                <th className="px-3 py-2.5 font-semibold text-slate-300">Dataset</th>
                <th className="px-3 py-2.5 text-right font-semibold text-slate-300">Draft Cases</th>
                <th className="px-3 py-2.5 font-semibold text-slate-300">Latest Version</th>
                <th className="hidden px-3 py-2.5 font-semibold text-slate-300 sm:table-cell">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {datasets.data.map((d) => (
                <tr key={d.id} className="hover:bg-surface-hover/80 transition-colors">
                  <td className="px-3 py-2.5">
                    <Link to={`/projects/${projectId}/datasets/${d.id}`} className="font-medium text-slate-100 hover:text-accent-400 transition-colors">
                      {d.name}
                    </Link>
                    {d.description && <p className="truncate text-xs text-slate-400">{d.description}</p>}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-xs tabular-nums text-slate-300">{d.draft_case_count}</td>
                  <td className="px-3 py-2.5 text-xs">
                    {d.latest_version ? (
                      <span className="font-mono text-slate-200">
                        <span className="rounded bg-surface-raised border border-border px-1.5 py-0.5 text-slate-200 font-semibold mr-1.5">
                          v{d.latest_version.version}
                        </span>
                        {d.latest_version.case_count} cases
                      </span>
                    ) : (
                      <span className="text-slate-500 font-mono">not published</span>
                    )}
                    {d.draft_changed_since_latest && d.latest_version && (
                      <span className="ml-2 rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-2xs text-amber-300 font-medium">
                        draft changed
                      </span>
                    )}
                  </td>
                  <td className="hidden px-3 py-2.5 text-xs text-slate-400 sm:table-cell">{formatRelative(d.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
