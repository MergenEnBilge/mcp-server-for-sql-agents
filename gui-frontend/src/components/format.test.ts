import { describe, expect, it } from "vitest";

import { endOfDayExclusive, plural, startOfDay, subjectLabel } from "./format";

describe("format helpers", () => {
  it("pluralises counts", () => {
    expect(plural(1, "entry", "entries")).toBe("1 entry");
    expect(plural(1204, "entry", "entries")).toBe("1,204 entries");
    expect(plural(0, "change")).toBe("0 changes");
  });

  it("makes an inclusive end date by pointing at the start of the next day", () => {
    const start = new Date(startOfDay("2026-09-21"));
    const end = new Date(endOfDayExclusive("2026-09-21"));
    expect(end.getTime() - start.getTime()).toBeGreaterThanOrEqual(23 * 3600_000);
    expect(end.getTime() - start.getTime()).toBeLessThanOrEqual(25 * 3600_000); // DST days
  });

  it("prefers a person's name over their id", () => {
    expect(subjectLabel({ subject_id: "u-1", display_name: "Ana" })).toBe("Ana");
    expect(subjectLabel({ subject_id: "u-1", display_name: null })).toBe("u-1");
  });
});
