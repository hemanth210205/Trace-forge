import { useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

import { ApiError } from "../api/client";
import type { SpanKind, Status } from "../api/types";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

const STATUS_STYLE: Record<Status, { dot: string; text: string; label: string }> = {
  ok: { dot: "bg-emerald-600", text: "text-emerald-700", label: "OK" },
  error: { dot: "bg-red-600", text: "text-red-700", label: "Error" },
  unset: { dot: "bg-ink-300", text: "text-ink-500", label: "Unset" },
};

export function StatusBadge({ status, compact = false }: { status: Status; compact?: boolean }) {
  const style = STATUS_STYLE[status] ?? STATUS_STYLE.unset;
  return (
    <span className={cx("inline-flex items-center gap-1.5 font-medium", style.text)} title={`Status: ${style.label}`}>
      <span className={cx("h-2 w-2 shrink-0 rounded-full", style.dot)} aria-hidden />
      {!compact && <span className="text-xs">{style.label}</span>}
      {compact && <span className="sr-only">{style.label}</span>}
    </span>
  );
}

export const KIND_STYLE: Record<SpanKind, { bar: string; tag: string }> = {
  llm: { bar: "bg-amber-500", tag: "border-amber-300 bg-amber-50 text-amber-800" },
  tool: { bar: "bg-teal-600", tag: "border-teal-300 bg-teal-50 text-teal-800" },
  retrieval: { bar: "bg-sky-700", tag: "border-sky-300 bg-sky-50 text-sky-800" },
  agent: { bar: "bg-ink-700", tag: "border-ink-300 bg-ink-100 text-ink-900" },
  chain: { bar: "bg-stone-500", tag: "border-stone-300 bg-stone-50 text-stone-700" },
  custom: { bar: "bg-ink-500", tag: "border-ink-200 bg-white text-ink-700" },
};

export function KindTag({ kind }: { kind: SpanKind }) {
  const style = KIND_STYLE[kind] ?? KIND_STYLE.custom;
  return (
    <span className={cx("inline-block rounded border px-1 font-mono text-2xs uppercase tracking-wide", style.tag)}>
      {kind}
    </span>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" };

export function Button({ variant = "secondary", className, ...props }: ButtonProps) {
  return (
    <button
      {...props}
      className={cx(
        // Fixed height: a wrapped label would overflow the button on narrow screens.
        "inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded border px-3 text-sm font-medium transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ink-900",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "border-ink-900 bg-ink-900 text-white hover:bg-ink-700",
        variant === "secondary" && "border-ink-200 bg-white text-ink-900 hover:border-ink-300 hover:bg-ink-50",
        variant === "danger" && "border-red-300 bg-white text-red-700 hover:bg-red-50",
        variant === "ghost" && "border-transparent bg-transparent text-ink-700 hover:bg-ink-100",
        className,
      )}
    />
  );
}

export function TextInput({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  // Full width unless the caller sets a width (two conflicting width utilities would resolve by CSS order).
  const hasWidth = /(^|\s)([a-z]+:)?w-/.test(className ?? "");
  return (
    <input
      {...props}
      className={cx(
        !hasWidth && "w-full",
        "h-8 rounded border border-ink-200 bg-white px-2.5 text-sm text-ink-900 placeholder:text-ink-500",
        "focus:border-ink-700 focus:outline-none focus:ring-1 focus:ring-ink-700",
        className,
      )}
    />
  );
}

export function SelectInput({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cx(
        "h-8 rounded border border-ink-200 bg-white px-2 text-sm text-ink-900 focus:border-ink-700 focus:outline-none focus:ring-1 focus:ring-ink-700",
        className,
      )}
    />
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-ink-500">{hint}</span>}
    </label>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-500" role="status">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink-200 border-t-ink-700" aria-hidden />
      {label}…
    </span>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="border border-dashed border-ink-200 bg-white px-6 py-10 text-center">
      <p className="text-sm font-semibold text-ink-900">{title}</p>
      {children && <div className="mx-auto mt-1.5 max-w-xl text-sm text-ink-500">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const code = error instanceof ApiError ? error.code : null;
  return (
    <div role="alert" className="border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <p className="font-semibold">{code === "network_error" ? "Backend unreachable" : "Could not load this data"}</p>
      <p className="mt-0.5">{error.message}</p>
      {code && code !== "network_error" && <p className="mt-0.5 font-mono text-2xs text-red-700">{code}</p>}
      {onRetry && (
        <Button className="mt-2" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="rounded px-1 font-sans text-2xs text-ink-500 hover:bg-ink-100 hover:text-ink-900"
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        });
      }}
    >
      {copied ? "Copied" : label}
    </button>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-1.5 flex items-center justify-between gap-2">
      <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-500">{children}</h3>
      {aside}
    </div>
  );
}
