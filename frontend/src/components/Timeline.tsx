import { useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

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

function countDescendants(node: { children: unknown[] }): number {
  let count = node.children.length;
  for (const child of node.children as { children: unknown[] }[]) {
    count += countDescendants(child);
  }
  return count;
}

/** Span hierarchy (left) aligned with a waterfall of duration bars on a shared time axis (right). */
export function Timeline({ timeline, collapsed, selectedId, onToggle, onSelect }: Props) {
  const rows = useMemo(() => visibleRows(timeline, collapsed), [timeline, collapsed]);

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface shadow-sm" role="tree" aria-label="Span timeline">
      {/* Waterfall Axis Header */}
      <div className="grid grid-cols-[minmax(11rem,42%)_1fr] border-b border-border bg-surface-raised text-2xs text-slate-400 md:grid-cols-[minmax(14rem,36%)_1fr]">
        <div className="px-3 py-2 font-medium text-slate-300">Span Hierarchy</div>
        <div className="relative mr-14 h-full">
          {TICKS.map((t) => (
            <span
              key={t}
              className={cx(
                "absolute top-2 -translate-x-1/2 whitespace-nowrap font-mono tabular-nums text-slate-400 first:translate-x-0 last:-translate-x-full",
                t !== 0 && t !== 1 && "hidden md:inline",
              )}
              style={{ left: `${t * 100}%` }}
            >
              {formatDuration(timeline.totalMs * t)}
            </span>
          ))}
        </div>
      </div>

      {/* Span Rows */}
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
                "grid cursor-pointer grid-cols-[minmax(11rem,42%)_1fr] border-b border-border-subtle text-xs transition-colors last:border-b-0 md:grid-cols-[minmax(14rem,36%)_1fr]",
                selected
                  ? "bg-accent-500/15 border-l-2 border-l-accent-500"
                  : "hover:bg-surface-hover/80",
              )}
              onClick={() => onSelect(span.id)}
            >
              {/* Span label & tree indent */}
              <div
                className="flex min-w-0 items-center gap-1.5 py-2 pr-2"
                style={{ paddingLeft: `${0.5 + node.depth * 0.9}rem` }}
              >
                {hasChildren ? (
                  <button
                    type="button"
                    className="grid h-4 w-4 shrink-0 place-items-center rounded text-slate-400 hover:bg-surface-hover hover:text-slate-100"
                    aria-label={isCollapsed ? `Expand ${span.name}` : `Collapse ${span.name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggle(span.id);
                    }}
                  >
                    {isCollapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                  </button>
                ) : (
                  <span className="w-4 shrink-0 text-center text-slate-600 font-mono text-2xs" aria-hidden>
                    ·
                  </span>
                )}
                <span
                  className={cx(
                    "h-2 w-2 shrink-0 rounded-full",
                    span.status === "error"
                      ? "bg-red-400"
                      : span.status === "ok"
                      ? "bg-emerald-400"
                      : "bg-slate-500",
                  )}
                  aria-hidden
                />
                <span
                  className={cx("truncate font-mono", selected ? "font-semibold text-accent-300" : "text-slate-200")}
                  title={span.name}
                >
                  {span.name}
                </span>
                <span className="hidden shrink-0 sm:inline ml-1">
                  <KindTag kind={span.kind} />
                </span>
                {node.orphan && (
                  <span className="shrink-0 font-mono text-2xs text-amber-400" title="Its parent span was not received">
                    orphan
                  </span>
                )}
                {isCollapsed && (
                  <span className="shrink-0 font-mono text-2xs text-slate-500">
                    +{countDescendants(node)}
                  </span>
                )}
              </div>

              {/* Waterfall Bar Column */}
              <div className="relative flex items-center border-l border-border-subtle pr-14">
                {TICKS.slice(1, -1).map((t) => (
                  <span
                    key={t}
                    className="absolute inset-y-0 w-px bg-border-subtle"
                    style={{ left: `${t * 100}%` }}
                    aria-hidden
                  />
                ))}
                <span
                  className={cx(
                    "relative h-3.5 min-w-[3px] rounded-sm transition-all",
                    KIND_STYLE[span.kind]?.bar ?? "bg-slate-500",
                    span.status === "error" && "ring-1 ring-red-400",
                  )}
                  style={{
                    left: `${geometry.leftPct}%`,
                    width: `${Math.max(geometry.widthPct, 0.4)}%`,
                  }}
                  title={`${span.name}: ${formatDuration(span.duration_ms)}`}
                />
                <span className="absolute right-2 font-mono text-2xs tabular-nums text-slate-400">
                  {formatDuration(span.duration_ms)}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
