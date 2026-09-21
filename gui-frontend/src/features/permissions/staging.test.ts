import { describe, expect, it } from "vitest";

import { cellKey, changes, effective, groupByRow, revocations, stage, type Pending } from "./staging";

const key = cellKey("user:ana", "orders");

describe("staging permission changes", () => {
  it("records a change that differs from what is saved", () => {
    const pending = stage(new Map(), key, false, true);
    expect(effective(pending, key, false)).toBe(true);
    expect(pending.size).toBe(1);
  });

  it("toggling twice is no change at all", () => {
    let pending: Pending = stage(new Map(), key, true, false);
    pending = stage(pending, key, true, true);
    expect(pending.size).toBe(0);
    expect(effective(pending, key, true)).toBe(true);
  });

  it("does not modify the map it was given", () => {
    const before: Pending = new Map();
    stage(before, key, false, true);
    expect(before.size).toBe(0);
  });

  it("groups changes by subject, because the API takes one call per subject", () => {
    let pending: Pending = new Map();
    pending = stage(pending, cellKey("user:ana", "orders"), false, true);
    pending = stage(pending, cellKey("user:ana", "payments"), true, false);
    pending = stage(pending, cellKey("role:analyst", "orders"), false, true);
    const grouped = groupByRow(pending);
    expect(grouped.get("user:ana")).toEqual([
      { col: "orders", granted: true },
      { col: "payments", granted: false },
    ]);
    expect(grouped.get("role:analyst")).toEqual([{ col: "orders", granted: true }]);
  });

  it("lists only revocations for the confirmation", () => {
    let pending: Pending = new Map();
    pending = stage(pending, cellKey("user:ana", "orders"), false, true);
    pending = stage(pending, cellKey("user:ana", "payments"), true, false);
    expect([...revocations(pending)]).toEqual([["user:ana", ["payments"]]]);
  });

  it("keeps table names containing colons intact", () => {
    const pending = stage(new Map(), cellKey("user:ana", "sales:2024"), false, true);
    expect(changes(pending)).toEqual([{ row: "user:ana", col: "sales:2024", granted: true }]);
  });
});
