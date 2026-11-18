import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { AlertTriangle, Key } from "lucide-react";

import { api } from "../api/client";
import type { ApiKey, ApiKeyCreated, Project } from "../api/types";
import { useApi } from "../api/useApi";
import { Button, Card, CopyButton, EmptyState, ErrorState, Spinner, TextInput, cx } from "../components/ui";
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
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between border-b border-border pb-3">
        <div>
          <div className="flex items-center gap-2">
            <Key className="h-5 w-5 text-slate-500" />
            <h1 className="text-xl font-semibold tracking-tight text-slate-100">API keys</h1>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Keys let the Python SDK send traces to{" "}
            <span className="font-semibold text-slate-200">{project.data?.name ?? "this project"}</span>. Only a hash
            is stored; a new key is shown once. Keys do not expire: revoke them when no longer needed.
          </p>
        </div>
      </div>

      {created && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-950/20 p-4 shadow-sm" role="status">
          <div className="flex items-center gap-2 text-sm font-semibold text-amber-300">
            <AlertTriangle className="h-4 w-4" />
            <p>Copy this key now — it will not be shown again.</p>
          </div>
          <div className="mt-2.5 flex items-center gap-2 rounded border border-amber-500/30 bg-surface px-3 py-2">
            <code className="min-w-0 flex-1 break-all font-mono text-xs text-amber-200" data-testid="new-api-key">
              {created.key}
            </code>
            <CopyButton value={created.key} />
          </div>
          <pre className="mt-3 overflow-x-auto rounded border border-border bg-sidebar p-3 font-mono text-xs leading-relaxed text-accent-300">
{`import traceforge

traceforge.init(api_key="${created.prefix ? `tf_${created.prefix}_…` : "…"}", endpoint="${window.location.origin.replace(":5173", ":8000")}")`}
          </pre>
          <Button variant="secondary" size="sm" className="mt-3" onClick={() => setCreated(null)}>
            I have stored it
          </Button>
        </div>
      )}

      <Card>
        <form onSubmit={create} className="flex flex-wrap items-center gap-2.5">
          <TextInput
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Key name, e.g. local-dev"
            required
            maxLength={100}
            className="max-w-xs"
            aria-label="Key name"
          />
          <Button variant="primary" type="submit">
            Create key
          </Button>
        </form>
        {error && <p role="alert" className="mt-2 text-xs text-red-400">{error}</p>}
      </Card>

      {keys.loading && !keys.data && (
        <div className="py-8 text-center">
          <Spinner label="Loading keys" />
        </div>
      )}
      {keys.error && <ErrorState error={keys.error} onRetry={keys.reload} />}
      {keys.data && keys.data.length === 0 && (
        <EmptyState title="No API keys">Create a key to start sending traces.</EmptyState>
      )}

      {keys.data && keys.data.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border bg-surface shadow-sm">
          <table className="w-full text-sm border-collapse">
            <thead className="border-b border-border bg-surface-raised text-left text-xs font-medium text-slate-400">
              <tr>
                <th className="px-3 py-2.5 font-semibold text-slate-300">Name</th>
                <th className="px-3 py-2.5 font-semibold text-slate-300">Key</th>
                <th className="px-3 py-2.5 font-semibold text-slate-300">Created</th>
                <th className="px-3 py-2.5 font-semibold text-slate-300">Last used</th>
                <th className="px-3 py-2.5 font-semibold text-slate-300">Status</th>
                <th className="px-3 py-2.5 text-right" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {keys.data.map((key) => (
                <tr key={key.id} className={cx("hover:bg-surface-hover/80 transition-colors", key.revoked_at ? "text-slate-500 opacity-60" : "text-slate-200")}>
                  <td className="px-3 py-2.5 font-medium">{key.name}</td>
                  <td className="px-3 py-2.5 font-mono text-xs text-slate-200">tf_{key.prefix}_••••</td>
                  <td className="px-3 py-2.5 text-xs text-slate-400" title={formatTimestamp(key.created_at)}>
                    {formatRelative(key.created_at)}
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-400 font-mono">
                    {key.last_used_at ? formatRelative(key.last_used_at) : "never"}
                  </td>
                  <td className="px-3 py-2.5 text-xs">
                    {key.revoked_at ? (
                      <span className="rounded border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 text-2xs font-mono text-red-400">
                        revoked
                      </span>
                    ) : (
                      <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-2xs font-mono text-emerald-400">
                        active
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {!key.revoked_at && (
                      <Button variant="ghost" size="sm" className="text-red-400 hover:text-red-300 text-xs" onClick={() => void revoke(key)}>
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
