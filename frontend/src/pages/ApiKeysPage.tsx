import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api/client";
import type { ApiKey, ApiKeyCreated, Project } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, CopyButton, EmptyState, ErrorState, Spinner, TextInput } from "../components/ui";
import { formatRelative, formatTimestamp } from "../lib/format";

export function ApiKeysPage() {
  const { projectId = "" } = useParams();
  const project = useApi((signal) => api<Project>(`/projects/${projectId}`, { signal }), [projectId]);
  const keys = useApi((signal) => api<ApiKey[]>(`/projects/${projectId}/api-keys`, { signal }), [projectId]);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (project.error) return <ErrorState error={project.error} onRetry={project.reload} />;

  async function create(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      setCreated(await api<ApiKeyCreated>(`/projects/${projectId}/api-keys`, { method: "POST", body: { name } }));
      setName("");
      keys.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function revoke(key: ApiKey) {
    if (!window.confirm(`Revoke "${key.name}" (${key.prefix})? Clients using it stop working immediately.`)) return;
    try {
      await api(`/projects/${projectId}/api-keys/${key.id}`, { method: "DELETE" });
      keys.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="text-lg font-semibold">API keys</h1>
      <p className="mb-4 text-sm text-ink-500">
        Keys let the Python SDK send traces to <span className="font-medium text-ink-900">{project.data?.name ?? "this project"}</span>.
        Only a hash is stored; a new key is shown once. Keys do not expire: revoke them when no longer needed.
      </p>

      {created && (
        <div className="mb-4 border border-amber-300 bg-amber-50 p-4" role="status">
          <p className="text-sm font-semibold text-amber-900">Copy this key now — it will not be shown again.</p>
          <div className="mt-2 flex items-center gap-2 border border-amber-200 bg-white px-2 py-1.5">
            <code className="min-w-0 flex-1 break-all font-mono text-xs" data-testid="new-api-key">
              {created.key}
            </code>
            <CopyButton value={created.key} />
          </div>
          <pre className="mt-3 overflow-x-auto bg-ink-900 p-3 font-mono text-xs leading-relaxed text-ink-100">
{`import traceforge

traceforge.init(api_key="${created.prefix ? `tf_${created.prefix}_…` : "…"}", endpoint="${window.location.origin.replace(":5173", ":8000")}")`}
          </pre>
          <Button className="mt-3" onClick={() => setCreated(null)}>
            I have stored it
          </Button>
        </div>
      )}

      <form onSubmit={create} className="mb-4 flex gap-2">
        <TextInput value={name} onChange={(e) => setName(e.target.value)} placeholder="Key name, e.g. local-dev" required maxLength={100} className="max-w-xs" aria-label="Key name" />
        <Button variant="primary" type="submit">
          Create key
        </Button>
      </form>
      {error && <p role="alert" className="mb-3 text-sm text-red-700">{error}</p>}

      {keys.loading && !keys.data && <Spinner label="Loading keys" />}
      {keys.error && <ErrorState error={keys.error} onRetry={keys.reload} />}
      {keys.data && keys.data.length === 0 && <EmptyState title="No API keys">Create a key to start sending traces.</EmptyState>}
      {keys.data && keys.data.length > 0 && (
        <div className="overflow-x-auto border border-ink-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-ink-200 bg-ink-50 text-left text-2xs uppercase tracking-wider text-ink-500">
              <tr>
                <th className="px-3 py-2 font-semibold">Name</th>
                <th className="px-3 py-2 font-semibold">Key</th>
                <th className="px-3 py-2 font-semibold">Created</th>
                <th className="px-3 py-2 font-semibold">Last used</th>
                <th className="px-3 py-2 font-semibold">Status</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {keys.data.map((key) => (
                <tr key={key.id} className={key.revoked_at ? "text-ink-500" : undefined}>
                  <td className="px-3 py-2 font-medium">{key.name}</td>
                  <td className="px-3 py-2 font-mono text-xs">tf_{key.prefix}_••••</td>
                  <td className="px-3 py-2 text-xs" title={formatTimestamp(key.created_at)}>{formatRelative(key.created_at)}</td>
                  <td className="px-3 py-2 text-xs">{key.last_used_at ? formatRelative(key.last_used_at) : "never"}</td>
                  <td className="px-3 py-2 text-xs">{key.revoked_at ? `revoked ${formatRelative(key.revoked_at)}` : "active"}</td>
                  <td className="px-3 py-2 text-right">
                    {!key.revoked_at && (
                      <Button variant="danger" className="h-7 px-2 text-xs" onClick={() => void revoke(key)}>
                        Revoke
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
