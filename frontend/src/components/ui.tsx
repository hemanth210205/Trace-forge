import { useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { AlertCircle, Check, Copy, Eye, EyeOff, Loader2 } from "lucide-react";

import { ApiError } from "../api/client";
import type { SpanKind, Status } from "../api/types";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

const STATUS_STYLE: Record<Status, { dot: string; text: string; bg: string; border: string; label: string }> = {
  ok: {
    dot: "bg-emerald-400",
    text: "text-emerald-400",
    bg: "bg-emerald-500/10",
    border: "border-emerald-500/20",
    label: "OK",
  },
  error: {
    dot: "bg-red-400",
    text: "text-red-400",
    bg: "bg-red-500/10",
    border: "border-red-500/20",
    label: "Error",
  },
  unset: {
    dot: "bg-slate-400",
    text: "text-slate-400",
    bg: "bg-slate-500/10",
    border: "border-slate-500/20",
    label: "Unset",
  },
};

export function StatusBadge({ status, compact = false }: { status: Status; compact?: boolean }) {
  const style = STATUS_STYLE[status] ?? STATUS_STYLE.unset;
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 font-medium",
        compact ? "py-0.5" : "rounded-full border px-2 py-0.5 text-xs",
        !compact && style.bg,
        !compact && style.border,
        style.text,
      )}
      title={`Status: ${style.label}`}
    >
      <span className={cx("h-1.5 w-1.5 shrink-0 rounded-full", style.dot)} aria-hidden />
      {!compact && <span className="text-2xs font-medium">{style.label}</span>}
      {compact && <span className="sr-only">{style.label}</span>}
    </span>
  );
}

export const KIND_STYLE: Record<SpanKind, { bar: string; tag: string }> = {
  // Two signal colours plus greys: lime for model calls, cyan for tools, dimmer lime for retrieval, greys for structure.
  llm: { bar: "bg-accent-400", tag: "border-accent-400/40 bg-accent-400/10 text-accent-300" },
  tool: { bar: "bg-pulse-400", tag: "border-pulse-400/40 bg-pulse-400/10 text-pulse-300" },
  retrieval: { bar: "bg-accent-400/45", tag: "border-accent-400/25 bg-transparent text-accent-200" },
  agent: { bar: "bg-slate-500", tag: "border-slate-600 bg-slate-800/60 text-slate-200" },
  chain: { bar: "bg-slate-600", tag: "border-slate-700 bg-slate-800/40 text-slate-300" },
  custom: { bar: "bg-slate-700", tag: "border-slate-700 bg-transparent text-slate-400" },
};

export function KindTag({ kind }: { kind: SpanKind }) {
  const style = KIND_STYLE[kind] ?? KIND_STYLE.custom;
  return (
    <span className={cx("inline-block rounded border px-1.5 py-0.2 font-mono text-2xs font-medium", style.tag)}>
      {kind}
    </span>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
};

export function Button({ variant = "secondary", size = "md", className, ...props }: ButtonProps) {
  return (
    <button
      {...props}
      className={cx(
        "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded font-medium transition-colors duration-100",
        "disabled:cursor-not-allowed disabled:opacity-40",
        size === "sm" && "h-7 px-2.5 text-xs",
        size === "md" && "h-8 px-3 text-[13px]",
        size === "lg" && "h-9 px-4 text-sm",
        variant === "primary" && "border border-accent-400 bg-accent-400 font-semibold text-[#09090B] hover:border-accent-300 hover:bg-accent-300",
        variant === "secondary" && "border border-border bg-surface-raised text-slate-200 hover:border-slate-700 hover:bg-surface-hover hover:text-slate-50",
        variant === "danger" && "border border-red-500/40 bg-red-950/40 text-red-300 hover:border-red-500/60 hover:bg-red-900/60 hover:text-red-200",
        variant === "ghost" && "border-transparent bg-transparent text-slate-400 hover:bg-surface-hover hover:text-slate-200",
        className,
      )}
    />
  );
}

export function TextInput({ className, large = false, ...props }: InputHTMLAttributes<HTMLInputElement> & { large?: boolean }) {
  const hasWidth = /(^|\s)([a-z]+:)?w-/.test(className ?? "");
  return (
    <input
      {...props}
      className={cx(
        !hasWidth && "w-full",
        large
          ? "h-11 rounded-xl border border-white/10 bg-black/50 px-3.5 text-[15px] text-slate-50 placeholder:text-slate-600 shadow-[inset_0_1px_2px_rgba(0,0,0,0.6)] transition-all hover:border-white/20 focus:border-accent-400/70 focus:bg-black/70 focus:outline-none focus:ring-4 focus:ring-accent-400/15"
          : "h-8 rounded border border-border bg-surface px-2.5 text-sm text-slate-100 placeholder:text-slate-500 transition-colors focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        className,
      )}
    />
  );
}

/**
 * Password field with a show/hide toggle. The toggle appears once something is typed. Its accessible name is
 * visually hidden text, not an aria-label, so it never competes with the field's own "Password" label.
 */
export function PasswordInput({ className, value, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & { value: string }) {
  const [visible, setVisible] = useState(false);
  const hasValue = value.length > 0;
  return (
    <div className="relative">
      <TextInput large {...props} value={value} type={visible && hasValue ? "text" : "password"} className={cx("pr-12", className)} />
      {hasValue && (
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-pressed={visible}
          className="absolute inset-y-0 right-1.5 my-auto grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition-colors hover:bg-white/[0.06] hover:text-slate-100"
          title={visible ? "Hide password" : "Show password"}
        >
          {visible ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
          <span className="sr-only">{visible ? "Hide password" : "Show password"}</span>
        </button>
      )}
    </div>
  );
}

export function TextArea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cx(
        "w-full rounded border border-border bg-surface p-2.5 text-sm text-slate-100 placeholder:text-slate-500 transition-colors",
        "focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent",
        "disabled:opacity-50 disabled:cursor-not-allowed font-mono text-xs leading-relaxed",
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
        "h-8 rounded border border-border bg-surface px-2.5 text-sm text-slate-100 transition-colors",
        "focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        className,
      )}
    />
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-slate-300">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-slate-400" role="status">
      <Loader2 className="h-4 w-4 animate-spin text-accent" aria-hidden />
      <span>{label}…</span>
    </span>
  );
}

export function EmptyState({
  title,
  children,
  action,
  icon,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-surface/30 px-6 py-12 text-center">
      {icon && <div className="mb-3 text-slate-500">{icon}</div>}
      <p className="text-sm font-semibold text-slate-200">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-slate-400">{children}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const code = error instanceof ApiError ? error.code : null;
  return (
    <div role="alert" className="rounded-lg border border-red-500/30 bg-red-950/20 px-4 py-3.5 text-sm text-red-200">
      <div className="flex items-start gap-2.5">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
        <div className="flex-1">
          <p className="font-semibold text-red-300">
            {code === "network_error" ? "Backend unreachable" : "Could not load this data"}
          </p>
          <p className="mt-1 text-xs text-red-200/80">{error.message}</p>
          {code && code !== "network_error" && (
            <p className="mt-1 font-mono text-2xs text-red-400/90">{code}</p>
          )}
          {onRetry && (
            <Button variant="danger" size="sm" className="mt-3" onClick={onRetry}>
              Retry
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-sans text-2xs text-slate-400 hover:bg-surface-hover hover:text-slate-200 transition-colors"
      title="Copy to clipboard"
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        });
      }}
    >
      {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
      <span>{copied ? "Copied" : label}</span>
    </button>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2 border-b border-border-subtle pb-1.5">
      <h3 className="text-[13px] font-semibold text-slate-200">{children}</h3>
      {aside}
    </div>
  );
}

export function Card({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("rounded-xl border border-white/[0.07] bg-surface/80 p-4 backdrop-blur-sm", className)}>
      {children}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  subvalue,
  change,
  tone = "neutral",
  icon,
}: {
  label: string;
  value: ReactNode;
  subvalue?: ReactNode;
  change?: string;
  tone?: "neutral" | "positive" | "negative" | "accent";
  icon?: ReactNode;
}) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-white/[0.07] bg-surface/80 p-4 backdrop-blur-sm">
      <div className="flex items-center justify-between text-slate-400">
        <span className="text-xs font-medium">{label}</span>
        {icon && <span className="text-slate-600">{icon}</span>}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className={cx(
          "text-2xl font-semibold tracking-tight tabular-nums",
          tone === "positive" && "text-emerald-400",
          tone === "negative" && "text-red-400",
          tone === "accent" && "text-accent-400",
          tone === "neutral" && "text-slate-100"
        )}>
          {value}
        </span>
        {change && (
          <span className={cx("text-xs font-medium", tone === "positive" ? "text-emerald-400" : "text-slate-400")}>
            {change}
          </span>
        )}
      </div>
      {subvalue && <div className="mt-1 text-xs text-slate-500">{subvalue}</div>}
    </div>
  );
}
