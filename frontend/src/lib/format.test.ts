import { describe, expect, it } from "vitest";

import { formatCost, formatDuration, formatNumber, formatRelative } from "./format";

describe("format", () => {
  it("formats durations across scales", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(0.42)).toBe("0.42 ms");
    expect(formatDuration(7.25)).toBe("7.3 ms");
    expect(formatDuration(431.6)).toBe("432 ms");
    expect(formatDuration(1234)).toBe("1.23 s");
    expect(formatDuration(125_000)).toBe("2m 05s");
  });

  it("formats costs from decimal strings without inventing precision", () => {
    expect(formatCost("0.004200")).toBe("$0.0042");
    expect(formatCost("12.500000")).toBe("$12.5");
    expect(formatCost("0")).toBe("$0");
    expect(formatCost(null)).toBe("—");
  });

  it("formats counts and relative times", () => {
    expect(formatNumber(12345)).toBe("12,345");
    expect(formatNumber(null)).toBe("—");
    const now = Date.parse("2026-10-30T12:00:00Z");
    expect(formatRelative("2026-10-30T11:59:30Z", now)).toBe("30s ago");
    expect(formatRelative("2026-10-30T09:00:00Z", now)).toBe("3h ago");
  });
});
