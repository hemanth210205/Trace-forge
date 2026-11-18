import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Database, Download, Plus, Trash2 } from "lucide-react";

import { api, ApiError } from "../api/client";
import type { Dataset, DatasetCase, EvaluationRun, VersionDetail, VersionSummary } from "../api/types";
import { useApi } from "../api/useApi";
import { RunStatusBadge, percent, preview } from "../components/evaluation";
import { Button, Card, EmptyState, ErrorState, Field, SelectInput, Spinner, TextInput, cx } from "../components/ui";
import { formatRelative, formatTimestamp } from "../lib/format";

type Tab = "cases" | "versions" | "import" | "evaluations";
const TABS: [Tab, string][] = [
  ["cases", "Draft cases"],
  ["versions", "Versions"],
  ["import", "Import / export"],
  ["evaluations", "Evaluations"],
];

/** Text fields accept JSON; anything that is not valid JSON is kept as a plain string. Empty means "none". */
export function parseLoose(text: string): unknown {
  if (!text.trim()) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

const toText = (value: unknown) =>
  value === null || value === undefined ? "" : typeof value === "string" ? value : JSON.stringify(value, null, 2);

function errorMessage(err: unknown): string {
  if (err instanceof ApiError && err.code === "validation_error" && Array.isArray(err.details)) {
    return (err.details as { loc: unknown[]; msg: string }[]).map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("; ");
  }
  return (err as Error).message;
}

export function DatasetPage() {
  const { projectId = "", datasetId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "cases";
  const navigate = useNavigate();
  const base = `/projects/${projectId}/datasets/${datasetId}`;
  const dataset = useApi((signal) => api<Dataset>(base, { signal }), [base]);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string>();

  if (dataset.error) {
    return dataset.error instanceof ApiError && dataset.error.status === 404 ? (
      <EmptyState
        title="Dataset not found"
        action={
          <Link className="text-sm underline text-accent" to={`/projects/${projectId}/datasets`}>
            All datasets
          </Link>
        }
      />
    ) : (
      <ErrorState error={dataset.error} onRetry={dataset.reload} />
    );
  }
  if (!dataset.data) {
    return (
      <div className="py-12 text-center">
        <Spinner label="Loading dataset" />
      </div>
    );
  }
  const d = dataset.data;

  async function remove() {
    if (
      !window.confirm(
        `Delete "${d.name}" with all its versions, evaluation runs, diagnoses, experiments and promotion decisions? This cannot be undone.`,
      )
    )
      return;
    setDeleting(true);
    setDeleteError(undefined);
    try {
      await api(base, { method: "DELETE" });
      navigate(`/projects/${projectId}/datasets`);
    } catch (err) {
      setDeleteError(`Could not delete the dataset: ${errorMessage(err)}`);
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Back button */}
      <div>
        <Link
          to={`/projects/${projectId}/datasets`}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-400 hover:text-slate-100 transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Datasets
        </Link>
      </div>

      {/* Dataset Header Card */}
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <Database className="h-5 w-5 text-slate-500" />
              <h1 className="text-xl font-semibold tracking-tight text-slate-100">{d.name}</h1>
            </div>
            {d.description && <p className="mt-1 text-xs text-slate-400">{d.description}</p>}
            <div className="mt-2 flex flex-wrap items-center gap-x-2 text-xs text-slate-400 font-mono">
              <span className="text-slate-300">
                {d.draft_case_count} draft case{d.draft_case_count === 1 ? "" : "s"}
              </span>
              <span>·</span>
              <span>
                {d.latest_version
                  ? `latest version v${d.latest_version.version} (${d.latest_version.case_count} cases)`
                  : "no published version"}
              </span>
              {d.draft_changed_since_latest && (
                <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.2 text-2xs text-amber-300">
                  draft has unpublished changes
                </span>
              )}
            </div>
          </div>
          <div>
            <Button
              variant="danger"
              size="sm"
              disabled={deleting}
              onClick={() => void remove()}
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span>{deleting ? "Deleting…" : "Delete dataset"}</span>
            </Button>
          </div>
          {deleteError && (
            <p role="alert" className="w-full text-xs text-red-400 font-medium">
              {deleteError}
            </p>
          )}
        </div>
      </Card>

      {/* Secondary Tab Navigation */}
      <nav className="flex gap-2 border-b border-border pb-1" aria-label="Dataset">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setParams(key === "cases" ? {} : { tab: key }, { replace: true })}
            className={cx(
              "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
              tab === key
                ? "bg-surface-raised text-accent-400 font-semibold border border-border"
                : "text-slate-400 hover:bg-surface hover:text-slate-200",
            )}
          >
            {label}
          </button>
        ))}
      </nav>

      {/* Active Tab View */}
      {tab === "cases" && <CasesTab base={base} onChange={dataset.reload} />}
      {tab === "versions" && <VersionsTab base={base} dataset={d} onChange={dataset.reload} />}
      {tab === "import" && <ImportTab base={base} onChange={dataset.reload} />}
      {tab === "evaluations" && <EvaluationsTab projectId={projectId} dataset={d} />}
    </div>
  );
}

// --- draft cases ------------------------------------------------------------------------------------------

interface CaseForm {
  key: string;
  input: string;
  expected: string;
  sources: string;
  metadata: string;
}
const EMPTY_FORM: CaseForm = { key: "", input: "", expected: "", sources: "", metadata: "" };

function CasesTab({ base, onChange }: { base: string; onChange: () => void }) {
  const cases = useApi((signal) => api<DatasetCase[]>(`${base}/cases`, { signal }), [base]);
  const [editing, setEditing] = useState<DatasetCase | "new" | null>(null);
  const [removeError, setRemoveError] = useState<string>();

  async function remove(c: DatasetCase) {
    if (!window.confirm(`Remove case "${c.key}" from the draft? Published versions keep it.`)) return;
    setRemoveError(undefined);
    try {
      await api(`${base}/cases/${c.id}`, { method: "DELETE" });
    } catch (err) {
      setRemoveError(`Could not remove case "${c.key}": ${errorMessage(err)}`);
      return;
    }
    cases.reload();
    onChange();
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-400">
          The draft is editable. Publishing freezes it as a new immutable version.
        </p>
        <Button variant="primary" size="sm" onClick={() => setEditing("new")}>
          <Plus className="h-3.5 w-3.5" />
          <span>Add case</span>
        </Button>
      </div>

      {removeError && <p role="alert" className="text-xs text-red-400">{removeError}</p>}

      {editing && (
        <CaseEditor
          base={base}
          existing={editing === "new" ? null : editing}
          onDone={() => {
            setEditing(null);
            cases.reload();
            onChange();
          }}
          onCancel={() => setEditing(null)}
        />
      )}

      {cases.error && <ErrorState error={cases.error} onRetry={cases.reload} />}
      {cases.loading && !cases.data && (
        <div className="py-8 text-center">
          <Spinner label="Loading cases" />
        </div>
      )}

      {cases.data && cases.data.length === 0 && !editing && (
        <EmptyState title="The draft has no cases">Add cases one by one or import a JSONL file.</EmptyState>
      )}

      {cases.data && cases.data.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border bg-surface shadow-sm">
          <table className="w-full table-fixed text-sm border-collapse">
            <thead className="border-b border-border bg-surface-raised text-left text-xs font-medium text-slate-400">
              <tr>
                <th className="w-44 px-3 py-2.5 font-semibold text-slate-300">Case Key</th>
                <th className="px-3 py-2.5 font-semibold text-slate-300">Input</th>
                <th className="px-3 py-2.5 font-semibold text-slate-300">Expected Output</th>
                <th className="hidden w-48 px-3 py-2.5 font-semibold text-slate-300 md:table-cell">Expected Sources</th>
                <th className="w-32 px-3 py-2.5 text-right font-semibold text-slate-300">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {cases.data.map((c) => (
                <tr key={c.id} className="align-top hover:bg-surface-hover/80 transition-colors">
                  <td className="truncate px-3 py-2.5 font-mono text-xs text-slate-200 font-medium">{c.key}</td>
                  <td className="truncate px-3 py-2.5 text-xs text-slate-300 font-mono" title={toText(c.input)}>
                    {preview(c.input)}
                  </td>
                  <td className="truncate px-3 py-2.5 text-xs text-slate-300 font-mono" title={toText(c.expected_output)}>
                    {preview(c.expected_output)}
                  </td>
                  <td className="hidden truncate px-3 py-2.5 font-mono text-2xs text-slate-400 md:table-cell">
                    {c.expected_sources.join(", ") || "—"}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button variant="ghost" size="sm" onClick={() => setEditing(c)}>
                      Edit
                    </Button>
                    <Button variant="ghost" size="sm" className="text-red-400 hover:text-red-300" onClick={() => void remove(c)}>
                      Remove
                    </Button>
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

function CaseEditor({
  base,
  existing,
  onDone,
  onCancel,
}: {
  base: string;
  existing: DatasetCase | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<CaseForm>(
    existing
      ? {
          key: existing.key,
          input: toText(existing.input),
          expected: toText(existing.expected_output),
          sources: existing.expected_sources.join("\n"),
          metadata: Object.keys(existing.metadata).length ? JSON.stringify(existing.metadata, null, 2) : "",
        }
      : EMPTY_FORM,
  );
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof CaseForm) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  async function save(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const metadata = parseLoose(form.metadata);
    if (metadata !== null && (typeof metadata !== "object" || Array.isArray(metadata))) {
      setError("Metadata must be a JSON object.");
      return;
    }
    const body = {
      ...(form.key.trim() ? { key: form.key.trim() } : {}),
      input: parseLoose(form.input),
      expected_output: parseLoose(form.expected),
      expected_sources: form.sources.split(/[\n,]/).map((s) => s.trim()).filter(Boolean),
      metadata: metadata ?? {},
    };
    try {
      if (existing) await api(`${base}/cases/${existing.id}`, { method: "PATCH", body });
      else await api(`${base}/cases`, { method: "POST", body });
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const area =
    "min-h-[5.5rem] w-full rounded border border-border bg-surface p-2.5 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent";
  return (
    <Card>
      <form onSubmit={save} className="grid gap-3 md:grid-cols-2" aria-label={existing ? `Edit case ${existing.key}` : "New case"}>
        <Field label="Key" hint="Optional; generated if empty. Letters, digits, . _ : -">
          <TextInput value={form.key} onChange={set("key")} maxLength={100} />
        </Field>
        <Field label="Expected sources" hint="One per line: document source or id the agent should retrieve.">
          <textarea className={area} value={form.sources} onChange={set("sources")} />
        </Field>
        <Field label="Input" hint="JSON, or plain text. Passed to your agent.">
          <textarea className={area} value={form.input} onChange={set("input")} required />
        </Field>
        <Field label="Expected output" hint="JSON, or plain text. Leave empty if only sources/fields are checked.">
          <textarea className={area} value={form.expected} onChange={set("expected")} />
        </Field>
        <Field label="Metadata" hint="Optional JSON object.">
          <textarea className={area} value={form.metadata} onChange={set("metadata")} />
        </Field>
        <div className="flex items-end gap-2">
          <Button variant="primary" type="submit">
            {existing ? "Save case" : "Add case"}
          </Button>
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
        {error && <p role="alert" className="text-xs text-red-400 md:col-span-2">{error}</p>}
      </form>
    </Card>
  );
}

// --- versions ---------------------------------------------------------------------------------------------

function VersionsTab({ base, dataset, onChange }: { base: string; dataset: Dataset; onChange: () => void }) {
  const versions = useApi((signal) => api<VersionSummary[]>(`${base}/versions`, { signal }), [
    base,
    dataset.updated_at,
    dataset.latest_version?.id,
  ]);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const next = (dataset.latest_version?.version ?? 0) + 1;
  const canPublish = dataset.draft_case_count > 0 && dataset.draft_changed_since_latest;

  async function publish(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api(`${base}/versions`, { method: "POST", body: { notes } });
      setNotes("");
      versions.reload();
      onChange();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <form onSubmit={publish} className="flex flex-wrap items-end gap-3">
          <Field
            label={`Publish the draft as v${next}`}
            hint={
              canPublish
                ? "Published versions can never be changed."
                : dataset.draft_case_count === 0
                ? "The draft is empty."
                : "The draft is identical to the latest version."
            }
          >
            <TextInput
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Release notes (optional)"
              className="w-80"
              maxLength={2000}
            />
          </Field>
          <Button variant="primary" type="submit" disabled={!canPublish}>
            Publish v{next}
          </Button>
          {error && <p role="alert" className="w-full text-xs text-red-400">{error}</p>}
        </form>
      </Card>

      {versions.error && <ErrorState error={versions.error} onRetry={versions.reload} />}
      {versions.data && versions.data.length === 0 && (
        <EmptyState title="No versions yet">Publish the draft to create version 1.</EmptyState>
      )}

      {versions.data && versions.data.length > 0 && (
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface shadow-sm">
          {versions.data.map((v) => (
            <li key={v.id} className="p-3">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="font-mono font-semibold text-slate-200">v{v.version}</span>
                <span className="text-xs text-slate-400 font-mono">
                  {v.case_count} cases · {formatRelative(v.created_at)}
                </span>
                <span className="font-mono text-2xs text-slate-500" title="Content hash">
                  {v.content_hash.slice(0, 12)}
                </span>
                {v.notes && <span className="text-xs text-slate-300 font-sans">{v.notes}</span>}
                <span className="ml-auto flex items-center gap-2">
                  <a
                    className="inline-flex items-center gap-1 rounded border border-border bg-surface px-2 py-1 text-xs text-slate-300 hover:bg-surface-hover hover:text-slate-100 transition-colors"
                    href={`/api/v1${base}/export?version=${v.version}&format=jsonl`}
                  >
                    <Download className="h-3 w-3" />
                    <span>Export JSONL</span>
                  </a>
                  <Button variant="ghost" size="sm" onClick={() => setOpen(open === v.version ? null : v.version)}>
                    {open === v.version ? "Hide cases" : "Show cases"}
                  </Button>
                </span>
              </div>
              {open === v.version && <VersionCases base={base} version={v.version} />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function VersionCases({ base, version }: { base: string; version: number }) {
  const detail = useApi((signal) => api<VersionDetail>(`${base}/versions/${version}`, { signal }), [base, version]);
  if (detail.error) return <ErrorState error={detail.error} />;
  if (!detail.data) return <Spinner />;
  return (
    <ol className="mt-3 space-y-1.5 border-l-2 border-border pl-3 text-xs font-mono">
      {detail.data.cases.map((c) => (
        <li key={c.id} className="text-slate-300">
          <span className="text-slate-200 font-semibold">{c.key}</span>{" "}
          <span className="text-slate-500">
            {preview(c.input, 70)} → {preview(c.expected_output, 70)}
          </span>
        </li>
      ))}
    </ol>
  );
}

// --- import / export --------------------------------------------------------------------------------------

function ImportTab({ base, onChange }: { base: string; onChange: () => void }) {
  const [format, setFormat] = useState<"jsonl" | "json">("jsonl");
  const [mode, setMode] = useState<"append" | "replace">("append");
  const [content, setContent] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [problems, setProblems] = useState<{ message: string; details: { at: string; message: string }[] } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setResult(null);
    setProblems(null);
    try {
      const r = await api<{ imported: number; draft_case_count: number }>(`${base}/import`, {
        method: "POST",
        body: { format, content, mode },
      });
      setResult(`Imported ${r.imported} case(s); the draft now has ${r.draft_case_count}.`);
      setContent("");
      onChange();
    } catch (err) {
      if (err instanceof ApiError && Array.isArray(err.details))
        setProblems({ message: err.message, details: err.details as { at: string; message: string }[] });
      else setProblems({ message: (err as Error).message, details: [] });
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <Card>
        <form onSubmit={submit} className="space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Format">
              <SelectInput value={format} onChange={(e) => setFormat(e.target.value as "jsonl" | "json")}>
                <option value="jsonl">JSONL (one case per line)</option>
                <option value="json">JSON array</option>
              </SelectInput>
            </Field>
            <Field label="Mode">
              <SelectInput value={mode} onChange={(e) => setMode(e.target.value as "append" | "replace")}>
                <option value="append">Append to draft</option>
                <option value="replace">Replace the draft</option>
              </SelectInput>
            </Field>
            <label className="cursor-pointer rounded border border-border bg-surface px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-surface-hover hover:text-slate-100 transition-colors">
              Choose file…
              <input
                type="file"
                accept=".jsonl,.json,.ndjson,application/json"
                className="sr-only"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void f.text().then(setContent);
                }}
              />
            </label>
          </div>
          <textarea
            aria-label="Import content"
            className="min-h-[12rem] w-full rounded border border-border bg-surface p-2.5 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-accent focus:outline-none"
            placeholder={
              '{"key": "shipping", "input": {"question": "Where is my order?"}, "expected_output": "...", "expected_sources": ["kb/shipping.md"]}'
            }
            value={content}
            onChange={(e) => setContent(e.target.value)}
            required
          />
          <Button variant="primary" type="submit">
            Validate and import
          </Button>
          {result && <p role="status" className="text-xs text-emerald-400 font-medium">{result}</p>}
          {problems && (
            <div role="alert" className="rounded-lg border border-red-500/30 bg-red-950/20 p-3 text-xs text-red-200">
              <p className="font-semibold text-red-300">{problems.message}</p>
              {problems.details.length > 0 && (
                <ul className="mt-1 list-disc space-y-0.5 pl-5">
                  {problems.details.map((d, i) => (
                    <li key={i}>
                      <span className="font-mono">{d.at}</span>: {d.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </form>
      </Card>

      <Card className="space-y-3 text-sm">
        <h3 className="font-semibold text-slate-200">Export the draft</h3>
        <p className="text-xs text-slate-400">
          Exports use the same format the importer accepts. Published versions can be exported from the Versions tab.
        </p>
        <div className="flex gap-2">
          <a
            className="rounded border border-border bg-surface px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-surface-hover hover:text-slate-100 transition-colors"
            href={`/api/v1${base}/export?format=jsonl`}
          >
            JSONL
          </a>
          <a
            className="rounded border border-border bg-surface px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-surface-hover hover:text-slate-100 transition-colors"
            href={`/api/v1${base}/export?format=json`}
          >
            JSON
          </a>
        </div>
        <h3 className="pt-2 font-semibold text-slate-200">Case fields</h3>
        <dl className="space-y-1 text-xs text-slate-400">
          <Def term="key">optional stable id</Def>
          <Def term="input">required; any JSON</Def>
          <Def term="expected_output">optional; any JSON</Def>
          <Def term="expected_sources">optional list of sources</Def>
          <Def term="metadata">optional object</Def>
        </dl>
      </Card>
    </div>
  );
}

function Def({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt className="inline font-mono text-slate-300">{term}</dt>{" "}
      <dd className="inline text-slate-400">— {children}</dd>
    </div>
  );
}

// --- evaluations ------------------------------------------------------------------------------------------

interface EvaluatorChoice {
  enabled: boolean;
  params: Record<string, string>;
}

const EVALUATOR_INFO: {
  type: string;
  label: string;
  help: string;
  params: { key: string; label: string; placeholder: string }[];
}[] = [
  {
    type: "exact_match",
    label: "Exact match",
    help: "Output equals the expected output.",
    params: [{ key: "field", label: "Output field", placeholder: "answer (optional)" }],
  },
  {
    type: "normalized_match",
    label: "Normalized match",
    help: "Equal after normalizing case, whitespace and punctuation.",
    params: [{ key: "field", label: "Output field", placeholder: "answer (optional)" }],
  },
  {
    type: "contains",
    label: "Contains keywords",
    help: "Output contains keywords (or the expected string).",
    params: [
      { key: "field", label: "Output field", placeholder: "answer (optional)" },
      { key: "keywords", label: "Keywords", placeholder: "comma-separated (optional)" },
    ],
  },
  {
    type: "json_match",
    label: "JSON structure",
    help: "Expected JSON is a structural subset of the output.",
    params: [{ key: "field", label: "Output field", placeholder: "(optional)" }],
  },
  {
    type: "source_match",
    label: "Source references",
    help: "Linked trace retrieved the expected sources.",
    params: [{ key: "min_recall", label: "Minimum recall", placeholder: "1.0" }],
  },
  {
    type: "required_fields",
    label: "Required fields",
    help: "Output has these non-empty fields.",
    params: [{ key: "fields", label: "Fields", placeholder: "answer, citations" }],
  },
];

function buildEvaluators(choices: Record<string, EvaluatorChoice>): Record<string, unknown>[] {
  return EVALUATOR_INFO.filter((info) => choices[info.type]?.enabled).map(({ type }) => {
    const p = choices[type].params;
    const config: Record<string, unknown> = { type };
    if (p.field?.trim()) config.field = p.field.trim();
    if (type === "contains" && p.keywords?.trim()) config.keywords = p.keywords.split(",").map((k) => k.trim()).filter(Boolean);
    if (type === "source_match" && p.min_recall?.trim()) config.min_recall = Number(p.min_recall);
    if (type === "required_fields") config.fields = (p.fields ?? "").split(",").map((k) => k.trim()).filter(Boolean);
    return config;
  });
}

function EvaluationsTab({ projectId, dataset }: { projectId: string; dataset: Dataset }) {
  const navigate = useNavigate();
  const versions = useApi(
    (signal) => api<VersionSummary[]>(`/projects/${projectId}/datasets/${dataset.id}/versions`, { signal }),
    [dataset.id],
  );
  const runs = useApi(
    (signal) => api<EvaluationRun[]>(`/projects/${projectId}/eval-runs`, { query: { dataset_id: dataset.id }, signal }),
    [dataset.id],
  );
  const [versionId, setVersionId] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [choices, setChoices] = useState<Record<string, EvaluatorChoice>>({
    normalized_match: { enabled: true, params: { field: "" } },
    source_match: { enabled: true, params: { min_recall: "1.0" } },
  });
  const selectedVersion = versionId || versions.data?.[0]?.id || "";

  async function create(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const evaluators = buildEvaluators(choices);
    if (!evaluators.length) {
      setError("Select at least one evaluator.");
      return;
    }
    try {
      const run = await api<EvaluationRun>(`/projects/${projectId}/eval-runs`, {
        method: "POST",
        body: { dataset_version_id: selectedVersion, name: name || `${dataset.name} evaluation`, evaluators },
      });
      navigate(`/projects/${projectId}/evaluations/${run.id}`);
    } catch (err) {
      if (err instanceof ApiError && Array.isArray(err.details)) {
        setError(`${err.message} ${(err.details as { msg: string }[]).map((d) => d.msg).join("; ")}`);
      } else setError((err as Error).message);
    }
  }

  if (versions.data && versions.data.length === 0) {
    return (
      <EmptyState title="Publish a version first">
        Evaluation runs always use an exact, immutable dataset version.
      </EmptyState>
    );
  }
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <Card>
        <form onSubmit={create} className="space-y-4" aria-label="New evaluation run">
          <h3 className="text-sm font-semibold text-slate-200">New Evaluation Run</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Dataset version">
              <SelectInput className="w-full" value={selectedVersion} onChange={(e) => setVersionId(e.target.value)}>
                {versions.data?.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version} · {v.case_count} cases
                  </option>
                ))}
              </SelectInput>
            </Field>
            <Field label="Run name">
              <TextInput
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={`${dataset.name} evaluation`}
                maxLength={200}
              />
            </Field>
          </div>
          <fieldset className="space-y-2">
            <legend className="mb-2 text-xs font-medium text-slate-300">Deterministic evaluators</legend>
            {EVALUATOR_INFO.map((info) => {
              const choice = choices[info.type] ?? { enabled: false, params: {} };
              return (
                <div
                  key={info.type}
                  className={cx(
                    "rounded border p-2.5 transition-colors",
                    choice.enabled ? "border-accent/40 bg-surface-raised" : "border-border bg-surface",
                  )}
                >
                  <label className="flex items-start gap-2.5 text-xs cursor-pointer">
                    <input
                      type="checkbox"
                      className="mt-0.5 rounded border-border bg-surface text-accent focus:ring-accent"
                      checked={choice.enabled}
                      onChange={(e) =>
                        setChoices({ ...choices, [info.type]: { ...choice, enabled: e.target.checked } })
                      }
                    />
                    <span>
                      <span className="font-semibold text-slate-200">{info.label}</span>
                      <span className="block text-2xs text-slate-400 mt-0.5">{info.help}</span>
                    </span>
                  </label>
                  {choice.enabled && info.params.length > 0 && (
                    <div className="mt-2.5 grid gap-2 pl-6 sm:grid-cols-2">
                      {info.params.map((p) => (
                        <TextInput
                          key={p.key}
                          aria-label={`${info.label}: ${p.label}`}
                          placeholder={`${p.label}: ${p.placeholder}`}
                          value={choice.params[p.key] ?? ""}
                          onChange={(e) =>
                            setChoices({
                              ...choices,
                              [info.type]: { ...choice, params: { ...choice.params, [p.key]: e.target.value } },
                            })
                          }
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </fieldset>
          <Button variant="primary" type="submit" disabled={!selectedVersion}>
            Create run
          </Button>
          {error && <p role="alert" className="text-xs text-red-400 font-medium">{error}</p>}
        </form>
      </Card>
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-slate-200">Runs on this dataset</h3>
        {runs.error && <ErrorState error={runs.error} onRetry={runs.reload} />}
        {runs.data && runs.data.length === 0 && <EmptyState title="No runs yet" />}
        {runs.data && runs.data.length > 0 && <RunTable projectId={projectId} runs={runs.data} />}
      </div>
    </div>
  );
}

export function RunTable({
  projectId,
  runs,
  showDataset = false,
}: {
  projectId: string;
  runs: EvaluationRun[];
  showDataset?: boolean;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface shadow-sm">
      <table className="w-full text-sm border-collapse">
        <thead className="border-b border-border bg-surface-raised text-left text-xs font-medium text-slate-400">
          <tr>
            <th className="px-3 py-2.5 font-semibold text-slate-300">Run</th>
            {showDataset && <th className="px-3 py-2.5 font-semibold text-slate-300">Dataset</th>}
            <th className="px-3 py-2.5 font-semibold text-slate-300">Status</th>
            <th className="px-3 py-2.5 text-right font-semibold text-slate-300">Pass rate</th>
            <th className="hidden px-3 py-2.5 text-right font-semibold text-slate-300 sm:table-cell">Cases</th>
            <th className="hidden px-3 py-2.5 font-semibold text-slate-300 md:table-cell">Created</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {runs.map((r) => (
            <tr key={r.id} className="hover:bg-surface-hover/80 transition-colors">
              <td className="px-3 py-2.5">
                <Link to={`/projects/${projectId}/evaluations/${r.id}`} className="font-medium text-slate-100 hover:text-accent-400 transition-colors">
                  {r.name}
                </Link>
              </td>
              {showDataset && (
                <td className="px-3 py-2.5 text-xs text-slate-300">
                  {r.dataset_name} <span className="font-mono text-slate-500">v{r.dataset_version}</span>
                </td>
              )}
              <td className="px-3 py-2.5">
                <RunStatusBadge status={r.status} />
              </td>
              <td
                className="px-3 py-2.5 text-right font-mono text-xs tabular-nums text-slate-200"
                title={r.status === "completed" ? undefined : "Pass rate is only reported for completed runs"}
              >
                {percent(r.pass_rate)}
              </td>
              <td className="hidden px-3 py-2.5 text-right font-mono text-xs tabular-nums text-slate-300 sm:table-cell">
                {r.received_cases}/{r.total_cases}
              </td>
              <td className="hidden px-3 py-2.5 text-xs text-slate-400 md:table-cell" title={formatTimestamp(r.created_at)}>
                {formatRelative(r.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
