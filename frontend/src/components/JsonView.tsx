import { CopyButton } from "./ui";

function isTruncated(value: unknown): value is { _truncated: true; original_bytes?: number; preview?: string } {
  return typeof value === "object" && value !== null && (value as Record<string, unknown>)._truncated === true;
}

/** Pretty-prints captured payloads as data (never as markup). Shows the SDK's truncation marker plainly. */
export function JsonView({ value, emptyLabel = "Not captured" }: { value: unknown; emptyLabel?: string }) {
  // An empty array is a real captured value (e.g. "no documents"); only absent values and empty objects are "empty".
  const emptyObject = typeof value === "object" && value !== null && !Array.isArray(value) && Object.keys(value).length === 0;
  if (value === null || value === undefined || emptyObject) {
    return <p className="text-xs italic text-ink-500">{emptyLabel}</p>;
  }
  if (isTruncated(value)) {
    return (
      <div className="space-y-1">
        <p className="text-xs text-amber-800">
          Truncated by the SDK ({value.original_bytes?.toLocaleString("en-US") ?? "?"} bytes). Preview:
        </p>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words border border-ink-200 bg-ink-50 p-2 font-mono text-xs text-ink-900">
          {value.preview ?? ""}
        </pre>
      </div>
    );
  }
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <div className="relative">
      <div className="absolute right-1 top-1">
        <CopyButton value={text} />
      </div>
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border border-ink-200 bg-ink-50 p-2 pr-12 font-mono text-xs leading-relaxed text-ink-900">
        {text}
      </pre>
    </div>
  );
}
