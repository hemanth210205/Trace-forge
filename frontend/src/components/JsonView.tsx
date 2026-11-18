import { CopyButton } from "./ui";

function isTruncated(value: unknown): value is { _truncated: true; original_bytes?: number; preview?: string } {
  return typeof value === "object" && value !== null && (value as Record<string, unknown>)._truncated === true;
}

/** Pretty-prints captured payloads as data (never as markup). Shows the SDK's truncation marker plainly. */
export function JsonView({ value, emptyLabel = "Not captured" }: { value: unknown; emptyLabel?: string }) {
  const emptyObject = typeof value === "object" && value !== null && !Array.isArray(value) && Object.keys(value).length === 0;
  if (value === null || value === undefined || emptyObject) {
    return <p className="text-xs italic text-slate-500">{emptyLabel}</p>;
  }
  if (isTruncated(value)) {
    return (
      <div className="space-y-1.5">
        <p className="text-xs text-amber-400">
          Truncated by the SDK ({value.original_bytes?.toLocaleString("en-US") ?? "?"} bytes). Preview:
        </p>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border border-border bg-surface p-2.5 font-mono text-xs text-slate-200">
          {value.preview ?? ""}
        </pre>
      </div>
    );
  }
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <div className="relative group">
      <div className="absolute right-1.5 top-1.5 z-10 opacity-70 group-hover:opacity-100 transition-opacity">
        <CopyButton value={text} />
      </div>
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border border-border bg-surface p-3 pr-14 font-mono text-xs leading-relaxed text-slate-200">
        {text}
      </pre>
    </div>
  );
}
