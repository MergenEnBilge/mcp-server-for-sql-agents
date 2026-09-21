import { describe, expect, it } from "vitest";

import { axisTicks, formatMs, formatRate, nearestIndex, niceCeil, runs, wholeCeil } from "./chartMath";

describe("chart math", () => {
  it("rounds an axis maximum up to a clean number", () => {
    expect(niceCeil(0)).toBe(1);
    expect(niceCeil(7)).toBe(10);
    expect(niceCeil(19)).toBe(20);
    expect(niceCeil(20)).toBe(20);
    expect(niceCeil(21)).toBe(50);
    expect(niceCeil(340)).toBe(500);
    expect(niceCeil(0.3)).toBeCloseTo(0.5);
  });

  it("keeps the halfway line on a whole number for counts", () => {
    expect(wholeCeil(5)).toBe(6);
    expect(axisTicks(wholeCeil(5))).toEqual([0, 3, 6]);
    expect(wholeCeil(1)).toBe(1);
    expect(wholeCeil(20)).toBe(20);
    expect(wholeCeil(0)).toBe(1);
  });

  it("puts gridlines at zero, half and the top", () => {
    expect(axisTicks(50)).toEqual([0, 25, 50]);
    expect(axisTicks(1)).toEqual([0, 1]);
  });

  it("snaps the pointer to the nearest slot and never outside the data", () => {
    expect(nearestIndex(0, 100, 10)).toBe(0);
    expect(nearestIndex(55, 100, 10)).toBe(5);
    expect(nearestIndex(-20, 100, 10)).toBe(0);
    expect(nearestIndex(999, 100, 10)).toBe(9);
    expect(nearestIndex(10, 100, 1)).toBe(0);
  });

  it("breaks a line where a reading is missing instead of drawing it as zero", () => {
    expect(runs([1, 2, null, 4, null, null, 7]).map((r) => r.map((p) => p.index))).toEqual([[0, 1], [3], [6]]);
    expect(runs([null, null])).toEqual([]);
  });

  it("formats latency and rates without lying", () => {
    expect(formatMs(184)).toBe("184 ms");
    expect(formatMs(12_400)).toBe("12.4 s");
    expect(formatMs(null)).toBe("–");
    expect(formatRate(null)).toBe("–");
    expect(formatRate(0)).toBe("0%");
    expect(formatRate(0.0004)).toBe("<0.1%");
    expect(formatRate(0.021)).toBe("2.1%");
    expect(formatRate(0.5)).toBe("50%");
  });
});
