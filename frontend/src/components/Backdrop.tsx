import { cx } from "./ui";

/**
 * Static decorative background: a faint grid, soft lime and cyan light, and a vignette.
 * No animation. Purely visual (aria-hidden, no pointer events).
 */
export function Backdrop({ intensity = "subtle", className }: { intensity?: "subtle" | "vivid"; className?: string }) {
  return (
    <div aria-hidden className={cx("pointer-events-none fixed inset-0 z-0 overflow-hidden", className)}>
      <div className={cx("absolute inset-0", intensity === "vivid" ? "tf-glow-vivid" : "tf-glow")} />
      <div className="tf-grid absolute inset-0" />
      <div className="tf-vignette absolute inset-0" />
    </div>
  );
}
