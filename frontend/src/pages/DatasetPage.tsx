import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { Dataset, DatasetCase, EvaluationRun, VersionDetail, VersionSummary } from "../api/types";
import { useApi } from "../api/useApi";
import { RunStatusBadge, percent, preview } from "../components/evaluation";
import { Button, EmptyState, ErrorState, Field, SelectInput, Spinner, TextInput, cx } from "../components/ui";
import { formatRelative, formatTimestamp } from "../lib/format";

type Tab = "cases" | "versions" | "import" | "evaluations";
const TABS: [Tab, string][] = [["cases", "Draft cases"], ["versions", "Versions"], ["import", "Import / export"], ["evaluations", "Evaluations"]];

/** Text fields accept JSON; anything that is not valid JSON is kept as a plain string. Empty means "none". */
export function parseLoose(text: string): unknown {
  if (!text.trim()) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

const toText = (value: unknown) => (value === null || value === undefined ? "" : typeof value === "string" ? value : JSON.stringify(value, null, 2));

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
      <EmptyState title="Dataset not found" action={<Link className="text-sm underline" to={`/projects/${projectId}/datasets`}>All datasets</Link>} />
    ) : (
      <ErrorState error={dataset.error} onRetry={dataset.reload} />
    );
  }
  if (!dataset.data) return <Spinner label="Loading dataset" />;
  const d = dataset.data;

  async function remove() {
    if (!window.confirm(`Delete "${d.name}" with all its versions, evaluation runs, diagnoses, experiments and promotion decisions? This cannot be undone.`)) return;
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
    <div className="mx-auto max-w-6xl space-y-4">
      <Link to={`/projects/${projectId}/datasets`} className="text-sm text-ink-500 hover:text-ink-900">← Datasets</Link>
      <header className="flex flex-wrap items-start gap-3 border border-ink-200 bg-white px-4 py-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold">{d.name}</h1>
          {d.description && <p className="text-sm text-ink-500">{d.description}</p>}
          <p className="mt-1 text-xs text-ink-500">
            {d.draft_case_count} draft case{d.draft_case_count === 1 ? "" : "s"} ·{" "}
            {d.latest_version ? `latest version v${d.latest_version.version} (${d.latest_version.case_count} cases)` : "no published version"}
            {d.draft_changed_since_latest && <span className="ml-1 text-amber-700">· draft has unpublished changes</span>}
          </p>
        </div>
        <Button variant="danger" className="h-7 text-xs" disabled={deleting} onClick={() => void remove()}>{deleting ? "Deleting…" : "Delete dataset"}</Button>
        {deleteError && <p role="alert" className="w-full text-sm text-red-700">{deleteError}</p>}
      </header>
      <nav className="flex gap-4 border-b border-ink-200" aria-label="Dataset">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setParams(key === "cases" ? {} : { tab: key }, { replace: true })}
            className={cx("-mb-px border-b-2 px-1 pb-2 text-sm", tab === key ? "border-ink-900 font-semibold" : "border-transparent text-ink-500 hover:text-ink-900")}
          >
            {label}
          </button>
        ))}
      </nav>
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
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-ink-500">The draft is editable. Publishing freezes it as a new immutable version.</p>
        <Button variant="primary" className="ml-auto" onClick={() => setEditing("new")}>Add case</Button>
      </div>
      {removeError && <p role="alert" className="text-sm text-red-700">{removeError}</p>}
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
      {cases.loading && !cases.data && <Spinner label="Loading cases" />}
      {cases.data && cases.data.length === 0 && !editing && (
        <EmptyState title="The draft has no cases">Add cases one by one or import a JSONL file.</EmptyState>
      )}
      {cases.data && cases.data.length > 0 && (
        <div className="overflow-x-auto border border-ink-200 bg-white">
          <table className="w-full table-fixed text-sm">
            <thead className="border-b border-ink-200 bg-ink-50 text-left text-2xs uppercase tracking-wider text-ink-500">
              <tr>
                <th className="w-44 px-3 py-2 font-semibold">Key</th>
                <th className="px-3 py-2 font-semibold">Input</th>
                <th className="px-3 py-2 font-semibold">Expected output</th>
                <th className="hidden w-48 px-3 py-2 font-semibold md:table-cell">Expected sources</th>
                <th className="w-32 px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {cases.data.map((c) => (
                <tr key={c.id} className="align-top">
                  <td className="truncate px-3 py-2 font-mono text-xs">{c.key}</td>
                  <td className="truncate px-3 py-2 text-xs" title={toText(c.input)}>{preview(c.input)}</td>
                  <td className="truncate px-3 py-2 text-xs" title={toText(c.expected_output)}>{preview(c.expected_output)}</td>
                  <td className="hidden truncate px-3 py-2 font-mono text-2xs md:table-cell">{c.expected_sources.join(", ") || "—"}</td>
                  <td className="px-3 py-1.5 text-right">
                    <Button variant="ghost" className="h-7 px-2 text-xs" onClick={() => setEditing(c)}>Edit</Button>
                    <Button variant="ghost" className="h-7 px-2 text-xs text-red-700" onClick={() => void remove(c)}>Remove</Button>
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

function CaseEditor({ base, existing, onDone, onCancel }: { base: string; existing: DatasetCase | null; onDone: () => void; onCancel: () => void }) {
  const [form, setForm] = useState<CaseForm>(
    existing
      ? { key: existing.key, input: toText(existing.input), expected: toText(existing.expected_output),
          sources: existing.expected_sources.join("\n"), metadata: Object.keys(existing.metadata).length ? JSON.stringify(existing.metadata, null, 2) : "" }
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

  const area = "min-h-[5.5rem] w-full rounded border border-ink-200 bg-white p-2 font-mono text-xs focus:border-ink-700 focus:outline-none focus:ring-1 focus:ring-ink-700";
  return (
    <form onSubmit={save} className="grid gap-3 border border-ink-200 bg-white p-4 md:grid-cols-2" aria-label={existing ? `Edit case ${existing.key}` : "New case"}>
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
        <Button variant="primary" type="submit">{existing ? "Save case" : "Add case"}</Button>
        <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
      {error && <p role="alert" className="text-sm text-red-700 md:col-span-2">{error}</p>}
    </form>
  );
}

// --- versions ---------------------------------------------------------------------------------------------

function VersionsTab({ base, dataset, onChange }: { base: string; dataset: Dataset; onChange: () => void }) {
  const versions = useApi((signal) => api<VersionSummary[]>(`${base}/versions`, { signal }), [base, dataset.updated_at, dataset.latest_version?.id]);
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
    <div className="space-y-3">
      <form onSubmit={publish} className="flex flex-wrap items-end gap-2 border border-ink-200 bg-white p-3">
        <Field label={`Publish the draft as v${next}`} hint={canPublish ? "Published versions can never be changed." : dataset.draft_case_count === 0 ? "The draft is empty." : "The draft is identical to the latest version."}>
          <TextInput value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Release notes (optional)" className="w-80" maxLength={2000} />
        </Field>
        <Button variant="primary" type="submit" disabled={!canPublish}>Publish v{next}</Button>
        {error && <p role="alert" className="w-full text-sm text-red-700">{error}</p>}
      </form>
      {versions.error && <ErrorState error={versions.error} onRetry={versions.reload} />}
      {versions.data && versions.data.length === 0 && <EmptyState title="No versions yet">Publish the draft to create version 1.</EmptyState>}
      {versions.data && versions.data.length > 0 && (
        <ul className="divide-y divide-ink-100 border border-ink-200 bg-white">
          {versions.data.map((v) => (
            <li key={v.id} className="px-3 py-2">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="font-mono font-semibold">v{v.version}</span>
                <span className="text-xs text-ink-500">{v.case_count} cases · {formatRelative(v.created_at)}</span>
                <span className="font-mono text-2xs text-ink-500" title="Content hash">{v.content_hash.slice(0, 12)}</span>
                {v.notes && <span className="text-xs text-ink-700">{v.notes}</span>}
                <span className="ml-auto flex gap-1">
                  <a className="rounded px-2 py-1 text-xs text-ink-700 hover:bg-ink-100" href={`/api/v1${base}/export?version=${v.version}&format=jsonl`}>Export JSONL</a>
                  <Button variant="ghost" className="h-7 px-2 text-xs" onClick={() => setOpen(open === v.version ? null : v.version)}>
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
    <ol className="mt-2 space-y-1 border-l-2 border-ink-100 pl-3 text-xs">
      {detail.data.cases.map((c) => (
        <li key={c.id}>
          <span className="font-mono">{c.key}</span> <span className="text-ink-500">{preview(c.input, 70)} → {preview(c.expected_output, 70)}</span>
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
      const r = await api<{ imported: number; draft_case_count: number }>(`${base}/import`, { method: "POST", body: { format, content, mode } });
      setResult(`Imported ${r.imported} case(s); the draft now has ${r.draft_case_count}.`);
      setContent("");
      onChange();
    } catch (err) {
      if (err instanceof ApiError && Array.isArray(err.details)) setProblems({ message: err.message, details: err.details as { at: string; message: string }[] });
      else setProblems({ message: (err as Error).message, details: [] });
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <form onSubmit={submit} className="space-y-3 border border-ink-200 bg-white p-4">
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
          <label className="cursor-pointer rounded border border-ink-200 px-3 py-1.5 text-sm hover:bg-ink-50">
            Choose file…
            <input type="file" accept=".jsonl,.json,.ndjson,application/json" className="sr-only"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void f.text().then(setContent); }} />
          </label>
        </div>
        <textarea
          aria-label="Import content"
          className="min-h-[12rem] w-full rounded border border-ink-200 p-2 font-mono text-xs focus:border-ink-700 focus:outline-none"
          placeholder={'{"key": "shipping", "input": {"question": "Where is my order?"}, "expected_output": "...", "expected_sources": ["kb/shipping.md"]}'}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          required
        />
        <Button variant="primary" type="submit">Validate and import</Button>
        {result && <p role="status" className="text-sm text-emerald-700">{result}</p>}
        {problems && (
          <div role="alert" className="border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            <p className="font-semibold">{problems.message}</p>
            {problems.details.length > 0 && (
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
                {problems.details.map((d, i) => <li key={i}><span className="font-mono">{d.at}</span>: {d.message}</li>)}
              </ul>
            )}
          </div>
        )}
      </form>
      <div className="space-y-2 border border-ink-200 bg-white p-4 text-sm">
        <h3 className="font-semibold">Export the draft</h3>
        <p className="text-xs text-ink-500">Exports use the same format the importer accepts. Published versions can be exported from the Versions tab.</p>
        <div className="flex gap-2">
          <a className="rounded border border-ink-200 px-3 py-1.5 text-sm hover:bg-ink-50" href={`/api/v1${base}/export?format=jsonl`}>JSONL</a>
          <a className="rounded border border-ink-200 px-3 py-1.5 text-sm hover:bg-ink-50" href={`/api/v1${base}/export?format=json`}>JSON</a>
        </div>
        <h3 className="pt-2 font-semibold">Case fields</h3>
        <dl className="space-y-1 text-xs text-ink-700">
          <Def term="key">optional stable id</Def>
          <Def term="input">required; any JSON</Def>
          <Def term="expected_output">optional; any JSON</Def>
          <Def term="expected_sources">optional list of sources</Def>
          <Def term="metadata">optional object</Def>
        </dl>
      </div>
    </div>
  );
}

function Def({ term, children }: { term: string; children: ReactNode }) {
  return <div><dt className="inline font-mono">{term}</dt> <dd className="inline text-ink-500">— {children}</dd></div>;
}

// --- evaluations ------------------------------------------------------------------------------------------

interface EvaluatorChoice {
  enabled: boolean;
  params: Record<string, string>;
}

const EVALUATOR_INFO: { type: string; label: string; help: string; params: { key: string; label: string; placeholder: string }[] }[] = [
  { type: "exact_match", label: "Exact match", help: "Output equals the expected output.", params: [{ key: "field", label: "Output field", placeholder: "answer (optional)" }] },
  { type: "normalized_match", label: "Normalized match", help: "Equal after normalizing case, whitespace and punctuation.", params: [{ key: "field", label: "Output field", placeholder: "answer (optional)" }] },
  { type: "contains", label: "Contains keywords", help: "Output contains keywords (or the expected string).", params: [{ key: "field", label: "Output field", placeholder: "answer (optional)" }, { key: "keywords", label: "Keywords", placeholder: "comma-separated (optional)" }] },
  { type: "json_match", label: "JSON structure", help: "Expected JSON is a structural subset of the output.", params: [{ key: "field", label: "Output field", placeholder: "(optional)" }] },
  { type: "source_match", label: "Source references", help: "Linked trace retrieved the expected sources.", params: [{ key: "min_recall", label: "Minimum recall", placeholder: "1.0" }] },
  { type: "required_fields", label: "Required fields", help: "Output has these non-empty fields.", params: [{ key: "fields", label: "Fields", placeholder: "answer, citations" }] },
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
  const versions = useApi((signal) => api<VersionSummary[]>(`/projects/${projectId}/datasets/${dataset.id}/versions`, { signal }), [dataset.id]);
  const runs = useApi((signal) => api<EvaluationRun[]>(`/projects/${projectId}/eval-runs`, { query: { dataset_id: dataset.id }, signal }), [dataset.id]);
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
    return <EmptyState title="Publish a version first">Evaluation runs always use an exact, immutable dataset version.</EmptyState>;
  }
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <form onSubmit={create} className="space-y-3 border border-ink-200 bg-white p-4" aria-label="New evaluation run">
        <h3 className="text-sm font-semibold">New evaluation run</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Dataset version">
            <SelectInput className="w-full" value={selectedVersion} onChange={(e) => setVersionId(e.target.value)}>
              {versions.data?.map((v) => <option key={v.id} value={v.id}>v{v.version} · {v.case_count} cases</option>)}
            </SelectInput>
          </Field>
          <Field label="Run name">
            <TextInput value={name} onChange={(e) => setName(e.target.value)} placeholder={`${dataset.name} evaluation`} maxLength={200} />
          </Field>
        </div>
        <fieldset className="space-y-2">
          <legend className="mb-1 text-xs font-medium text-ink-700">Deterministic evaluators</legend>
          {EVALUATOR_INFO.map((info) => {
            const choice = choices[info.type] ?? { enabled: false, params: {} };
            return (
              <div key={info.type} className={cx("border px-3 py-2", choice.enabled ? "border-ink-300" : "border-ink-100")}>
                <label className="flex items-start gap-2 text-sm">
                  <input type="checkbox" className="mt-1" checked={choice.enabled}
                    onChange={(e) => setChoices({ ...choices, [info.type]: { ...choice, enabled: e.target.checked } })} />
                  <span>
                    <span className="font-medium">{info.label}</span>
                    <span className="block text-xs text-ink-500">{info.help}</span>
                  </span>
                </label>
                {choice.enabled && info.params.length > 0 && (
                  <div className="mt-2 grid gap-2 pl-6 sm:grid-cols-2">
                    {info.params.map((p) => (
                      <TextInput key={p.key} aria-label={`${info.label}: ${p.label}`} placeholder={`${p.label}: ${p.placeholder}`}
                        value={choice.params[p.key] ?? ""}
                        onChange={(e) => setChoices({ ...choices, [info.type]: { ...choice, params: { ...choice.params, [p.key]: e.target.value } } })} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </fieldset>
        <Button variant="primary" type="submit" disabled={!selectedVersion}>Create run</Button>
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      </form>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Runs on this dataset</h3>
        {runs.error && <ErrorState error={runs.error} onRetry={runs.reload} />}
        {runs.data && runs.data.length === 0 && <EmptyState title="No runs yet" />}
        {runs.data && runs.data.length > 0 && <RunTable projectId={projectId} runs={runs.data} />}
      </div>
    </div>
  );
}

export function RunTable({ projectId, runs, showDataset = false }: { projectId: string; runs: EvaluationRun[]; showDataset?: boolean }) {
  return (
    <div className="overflow-x-auto border border-ink-200 bg-white">
      <table className="w-full text-sm">
        <thead className="border-b border-ink-200 bg-ink-50 text-left text-2xs uppercase tracking-wider text-ink-500">
          <tr>
            <th className="px-3 py-2 font-semibold">Run</th>
            {showDataset && <th className="px-3 py-2 font-semibold">Dataset</th>}
            <th className="px-3 py-2 font-semibold">Status</th>
            <th className="px-3 py-2 text-right font-semibold">Pass rate</th>
            <th className="hidden px-3 py-2 text-right font-semibold sm:table-cell">Cases</th>
            <th className="hidden px-3 py-2 font-semibold md:table-cell">Created</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {runs.map((r) => (
            <tr key={r.id} className="hover:bg-ink-50">
              <td className="px-3 py-2">
                <Link to={`/projects/${projectId}/evaluations/${r.id}`} className="font-medium hover:underline">{r.name}</Link>
              </td>
              {showDataset && <td className="px-3 py-2 text-xs">{r.dataset_name} <span className="font-mono text-ink-500">v{r.dataset_version}</span></td>}
              <td className="px-3 py-2"><RunStatusBadge status={r.status} /></td>
              <td className="px-3 py-2 text-right font-mono text-xs tabular-nums" title={r.status === "completed" ? undefined : "Pass rate is only reported for completed runs"}>
                {percent(r.pass_rate)}
              </td>
              <td className="hidden px-3 py-2 text-right font-mono text-xs tabular-nums sm:table-cell">{r.received_cases}/{r.total_cases}</td>
              <td className="hidden px-3 py-2 text-xs text-ink-500 md:table-cell" title={formatTimestamp(r.created_at)}>{formatRelative(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
