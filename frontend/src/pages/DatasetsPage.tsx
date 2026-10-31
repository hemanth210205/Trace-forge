import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { Dataset } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, EmptyState, ErrorState, Field, Spinner, TextInput } from "../components/ui";
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
    <div className="mx-auto max-w-5xl">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Datasets</h1>
          <p className="text-sm text-ink-500">Versioned test cases with expected outputs and source references.</p>
        </div>
        {!creating && (
          <Button variant="primary" onClick={() => setCreating(true)}>
            New dataset
          </Button>
        )}
      </div>
      {creating && (
        <form onSubmit={create} className="mb-4 grid gap-3 border border-ink-200 bg-white p-4 sm:grid-cols-[1fr_2fr_auto] sm:items-end">
          <Field label="Name">
            <TextInput value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} autoFocus />
          </Field>
          <Field label="Description">
            <TextInput value={description} onChange={(e) => setDescription(e.target.value)} maxLength={5000} />
          </Field>
          <div className="flex gap-2">
            <Button variant="primary" type="submit">Create</Button>
            <Button type="button" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
          </div>
          {error && <p role="alert" className="text-sm text-red-700 sm:col-span-3">{error}</p>}
        </form>
      )}
      {datasets.loading && !datasets.data && <Spinner label="Loading datasets" />}
      {datasets.error && <ErrorState error={datasets.error} onRetry={datasets.reload} />}
      {datasets.data && datasets.data.length === 0 && !creating && (
        <EmptyState title="No datasets yet" action={<Button variant="primary" onClick={() => setCreating(true)}>Create a dataset</Button>}>
          A dataset holds test cases: an input for your agent, the expected output, and the sources it should retrieve.
          Publish a version, then run an evaluation against it.
        </EmptyState>
      )}
      {datasets.data && datasets.data.length > 0 && (
        <div className="overflow-x-auto border border-ink-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-ink-200 bg-ink-50 text-left text-2xs uppercase tracking-wider text-ink-500">
              <tr>
                <th className="px-3 py-2 font-semibold">Dataset</th>
                <th className="px-3 py-2 text-right font-semibold">Draft cases</th>
                <th className="px-3 py-2 font-semibold">Latest version</th>
                <th className="hidden px-3 py-2 font-semibold sm:table-cell">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {datasets.data.map((d) => (
                <tr key={d.id} className="hover:bg-ink-50">
                  <td className="px-3 py-2">
                    <Link to={`/projects/${projectId}/datasets/${d.id}`} className="font-medium hover:underline">{d.name}</Link>
                    {d.description && <p className="truncate text-xs text-ink-500">{d.description}</p>}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs tabular-nums">{d.draft_case_count}</td>
                  <td className="px-3 py-2 text-xs">
                    {d.latest_version ? (
                      <span className="font-mono">v{d.latest_version.version} · {d.latest_version.case_count} cases</span>
                    ) : (
                      <span className="text-ink-500">not published</span>
                    )}
                    {d.draft_changed_since_latest && d.latest_version && <span className="ml-2 text-amber-700">draft changed</span>}
                  </td>
                  <td className="hidden px-3 py-2 text-xs text-ink-500 sm:table-cell">{formatRelative(d.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
