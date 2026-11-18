import type { ReactNode } from "react";

import { CopyButton, cx } from "./ui";

const KEYWORDS = new Set([
  "import", "from", "with", "as", "def", "return", "async", "await", "for", "in", "lambda", "if", "else", "None", "True", "False",
]);

// Comments, strings, decorators, keywords; everything else stays plain. Enough for the short snippets in the docs.
const TOKEN = /(#.*$)|("[^"]*"|'[^']*')|(@[\w.]+)|\b([A-Za-z_]+)\b/g;

function highlight(line: string, lang: "python" | "bash" | "json"): ReactNode {
  if (lang === "bash") {
    if (line.trimStart().startsWith("#")) return <span className="italic text-slate-500">{line}</span>;
    return <span className="text-accent-300">{line}</span>;
  }
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of line.matchAll(TOKEN)) {
    const [text, comment, str, deco, word] = m;
    const at = m.index ?? 0;
    if (word && !KEYWORDS.has(word)) continue;
    if (at > last) out.push(line.slice(last, at));
    if (comment) out.push(<span key={at} className="italic text-slate-500">{text}</span>);
    else if (str) out.push(<span key={at} className={lang === "json" ? "text-pulse-300" : "text-accent-300"}>{text}</span>);
    else if (deco) out.push(<span key={at} className="text-pulse-300">{text}</span>);
    else out.push(<span key={at} className="text-pulse-400">{text}</span>);
    last = at + text.length;
  }
  if (last < line.length) out.push(line.slice(last));
  return out;
}

/** A code panel with macOS-style window controls, a file name, line numbers and a copy button. */
export function CodeBlock({
  code,
  title,
  lang = "python",
  numbers = true,
  className,
}: {
  code: string;
  title?: string;
  lang?: "python" | "bash" | "json";
  numbers?: boolean;
  className?: string;
}) {
  const lines = code.replace(/\n$/, "").split("\n");
  return (
    <div className={cx("tf-glass relative overflow-hidden rounded-2xl", className)}>
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2.5">
        <span className="flex min-w-0 items-center gap-2 font-mono text-2xs text-slate-400">
          {/* Exact hex because the theme remaps Tailwind's yellow. */}
          <span className="flex shrink-0 gap-1.5" aria-hidden>
            <span className="h-2.5 w-2.5 rounded-full bg-[#FF5F57]" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#FEBC2E]" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#28C840]" />
          </span>
          <span className="truncate">{title ?? (lang === "bash" ? "terminal" : lang === "json" ? "data" : "python")}</span>
        </span>
        <CopyButton value={lines.filter((l) => lang !== "bash" || !l.trimStart().startsWith("#")).join("\n")} />
      </div>
      <pre className="overflow-x-auto px-4 py-4 font-mono text-xs leading-6 text-slate-200">
        {lines.map((line, i) => (
          <div key={i} className="flex">
            {numbers && <span className="mr-4 w-5 shrink-0 select-none text-right text-slate-700">{i + 1}</span>}
            <span className="whitespace-pre">
              {lang === "bash" && !line.trimStart().startsWith("#") && line.trim() && <span className="select-none text-slate-600">$ </span>}
              {highlight(line, lang)}
            </span>
          </div>
        ))}
      </pre>
    </div>
  );
}
