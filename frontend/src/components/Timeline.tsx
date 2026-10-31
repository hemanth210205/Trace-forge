import { useMemo } from "react";

import { barGeometry, visibleRows, type Timeline as TimelineData } from "../lib/spanTree";
import { formatDuration } from "../lib/format";
import { KIND_STYLE, KindTag, cx } from "./ui";

interface Props {
  timeline: TimelineData;
  collapsed: ReadonlySet<string>;
  selectedId: string | null;
  onToggle: (spanId: string) => void;
  onSelect: (spanId: string) => void;
}

const TICKS = [0, 0.25, 0.5, 0.75, 1];

/** Span hierarchy (left) aligned with a waterfall of duration bars on a shared time axis (right). */
export function Timeline({ timeline, collapsed, selectedId, onToggle, onSelect }: Props) {
  const rows = useMemo(() => visibleRows(timeline, collapsed), [timeline, collapsed]);

  return (
    <div className="border border-ink-200 bg-white" role="tree" aria-label="Span timeline">
      <div className="grid grid-cols-[minmax(11rem,40%)_1fr] border-b border-ink-200 bg-ink-50 text-2xs text-ink-500 md:grid-cols-[minmax(14rem,34%)_1fr]">
        <div className="px-3 py-1.5 font-semibold uppercase tracking-wider">Span</div>
        <div className="relative mr-14 h-full">
          {TICKS.map((t) => (
            <span
              key={t}
              className={cx(
                "absolute top-1.5 -translate-x-1/2 whitespace-nowrap font-mono tabular-nums first:translate-x-0 last:-translate-x-full",
                t !== 0 && t !== 1 && "hidden md:inline", // only start and end labels fit on narrow screens
              )}
              style={{ left: `${t * 100}%` }}
            >
              {formatDuration(timeline.totalMs * t)}
            </span>
          ))}
        </div>
      </div>
      <ul>
        {rows.map((node) => {
          const { span } = node;
          const geometry = barGeometry(span, timeline);
          const selected = span.id === selectedId;
          const hasChildren = node.children.length > 0;
          const isCollapsed = collapsed.has(span.id);
          return (
            <li
              key={span.id}
              role="treeitem"
              aria-selected={selected}
              aria-expanded={hasChildren ? !isCollapsed : undefined}
              aria-level={node.depth + 1}
              className={cx(
                "grid cursor-pointer grid-cols-[minmax(11rem,40%)_1fr] border-b border-ink-100 text-sm last:border-b-0 md:grid-cols-[minmax(14rem,34%)_1fr]",
                selected ? "bg-amber-50/70" : "hover:bg-ink-50",
              )}
              onClick={() => onSelect(span.id)}
            >
              <div className="flex min-w-0 items-center gap-1 py-1.5 pr-2" style={{ paddingLeft: `${0.5 + node.depth * 0.9}rem` }}>
                {hasChildren ? (
                  <button
                    type="button"
                    className="grid h-4 w-4 shrink-0 place-items-center font-mono text-xs text-ink-500 hover:text-ink-900"
                    aria-label={isCollapsed ? `Expand ${span.name}` : `Collapse ${span.name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggle(span.id);
                    }}
                  >
                    {isCollapsed ? "▸" : "▾"}
                  </button>
                ) : (
                  <span className="w-4 shrink-0 text-center text-ink-200" aria-hidden>
                    ·
                  </span>
                )}
                <span className={cx("h-2 w-2 shrink-0 rounded-full", span.status === "error" ? "bg-red-600" : span.status === "ok" ? "bg-emerald-600" : "bg-ink-300")} aria-hidden />
                <span className={cx("truncate", selected && "font-semibold")} title={span.name}>
                  {span.name}
                </span>
                <span className="hidden shrink-0 sm:inline">
                  <KindTag kind={span.kind} />
                </span>
                {node.orphan && (
                  <span className="shrink-0 font-mono text-2xs text-amber-700" title="Its parent span was not received">
                    orphan
                  </span>
                )}
                {isCollapsed && <span className="shrink-0 text-2xs text-ink-500">+{countDescendants(node)}</span>}
              </div>
              <div className="relative flex items-center border-l border-ink-100 pr-14">
                {TICKS.slice(1, -1).map((t) => (
                  <span key={t} className="absolute inset-y-0 w-px bg-ink-100" style={{ left: `${t * 100}%` }} aria-hidden />
                ))}
                <span
                  className={cx(
                    "relative h-3 min-w-[2px]",
                    KIND_STYLE[span.kind]?.bar ?? "bg-ink-500",
                    span.status === "error" && "outline outline-1 outline-offset-1 outline-red-600",
                    geometry.open && "bg-[length:6px_6px] opacity-60 [background-image:repeating-linear-gradient(45deg,transparent_0_2px,rgba(255,255,255,.6)_2px_4px)]",
                  )}
                  style={{ marginLeft: `${geometry.leftPct}%`, width: `${geometry.widthPct}%` }}
                  title={`${span.name}: ${geometry.open ? "not ended" : formatDuration(span.duration_ms)}`}
                />
                <span className="absolute right-1.5 font-mono text-2xs tabular-nums text-ink-500">
                  {geometry.open ? "open" : formatDuration(span.duration_ms)}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function countDescendants(node: { children: { children: unknown[] }[] }): number {
  let total = 0;
  const stack = [...node.children];
  while (stack.length) {
    const next = stack.pop() as { children: { children: unknown[] }[] };
    total += 1;
    stack.push(...next.children);
  }
  return total;
}
