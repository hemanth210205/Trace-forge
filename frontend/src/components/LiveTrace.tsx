import { useEffect, useState } from "react";
import { Bot, Database, MessageSquareText, Wrench } from "lucide-react";

import { cx } from "./ui";

// Illustrative run of the sample support agent (examples/sample_agent). Not live data.
const STEPS = [
  { name: "support-agent", kind: "agent", ms: 412, icon: Bot, note: "Question received: “Where is order A1?”" },
  { name: "kb-search", kind: "retrieval", ms: 74, icon: Database, note: "3 documents retrieved · top source kb/shipping.md" },
  { name: "order-lookup", kind: "tool", ms: 121, icon: Wrench, note: "lookup_order(“A1”) → shipped, arrives Friday" },
  { name: "compose-answer", kind: "llm", ms: 183, icon: MessageSquareText, note: "Answer written from 2 cited sources · 64 tokens" },
];

/** Animated pipeline for the landing hero: the active step moves along like a trace being recorded. */
export function LiveTrace() {
  const [active, setActive] = useState(0);
  const [run, setRun] = useState(2041);

  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const timer = setInterval(() => {
      setActive((i) => {
        if (i === STEPS.length - 1) setRun((r) => r + 1);
        return (i + 1) % STEPS.length;
      });
    }, 1600);
    return () => clearInterval(timer);
  }, []);

  const step = STEPS[active];

  return (
    <div aria-hidden className="tf-glass relative overflow-hidden rounded-2xl">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2.5 font-mono text-2xs">
        <span className="flex items-center gap-2 text-slate-300">
          <span className="tf-blink h-2 w-2 rounded-full bg-accent-400" />
          RECORDING TRACE
          <span className="hidden text-slate-600 sm:inline">#{run}</span>
        </span>
        <span className="text-slate-500">
          support-request · <span className="text-pulse-400">4 spans</span>
        </span>
      </div>

      <div className="tf-dots relative px-4 py-6 sm:px-6">
        {/* Pipeline */}
        <ol className="relative grid grid-cols-4 gap-2 sm:gap-4">
          <div className="absolute left-[12.5%] right-[12.5%] top-5 h-px bg-border" />
          <div
            className="absolute left-[12.5%] top-5 h-px bg-gradient-to-r from-pulse-400 to-accent-400 transition-all duration-700"
            style={{ width: `${(active / (STEPS.length - 1)) * 75}%` }}
          />
          {STEPS.map((s, i) => {
            const Icon = s.icon;
            const state = i === active ? "active" : i < active ? "done" : "idle";
            return (
              <li key={s.name} className="relative flex min-w-0 flex-col items-center text-center">
                <span
                  className={cx(
                    "relative z-10 grid h-10 w-10 place-items-center rounded-xl border transition-all duration-300",
                    state === "active" && "border-accent-400 bg-accent-400 text-[#09090B] shadow-[0_0_28px_rgba(184,255,0,0.35)]",
                    state === "done" && "border-pulse-500/60 bg-pulse-900/60 text-pulse-300",
                    state === "idle" && "border-border bg-surface text-slate-500",
                  )}
                >
                  <Icon className="h-4 w-4" />
                </span>
                <span className={cx("mt-2.5 w-full truncate font-mono text-[10px] sm:text-xs", state === "active" ? "text-slate-50" : "text-slate-400")}>
                  {s.name}
                </span>
                <span className="font-mono text-2xs text-slate-600">{s.kind}</span>
              </li>
            );
          })}
        </ol>

        {/* Waterfall */}
        <div className="mt-6 space-y-1.5">
          {STEPS.map((s, i) => {
            const left = [0, 2, 22, 54][i];
            const width = [100, 18, 30, 44][i];
            return (
              <div key={s.name} className="grid grid-cols-[1fr_44px] items-center gap-3">
                <div className="relative h-2 overflow-hidden rounded-full bg-white/[0.05]">
                  <div
                    className={cx("absolute inset-y-0 rounded-full transition-all duration-500", i <= active ? "opacity-100" : "opacity-25")}
                    style={{ left: `${left}%`, width: `${width}%`, background: i === active ? "#B8FF00" : i < active ? "#00BFD6" : "#3F3F46" }}
                  />
                </div>
                <span className="text-right font-mono text-2xs text-slate-500">{s.ms}ms</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="flex items-center gap-2 border-t border-white/[0.07] bg-black/20 px-4 py-2.5 font-mono text-2xs">
        <span className="text-accent-400">▸</span>
        <span className="shrink-0 text-slate-100">{step.name}</span>
        <span className="truncate text-slate-400">{step.note}</span>
      </div>
    </div>
  );
}
