import assert from "node:assert/strict";
import { test } from "node:test";
import { forceStopReady } from "../src/taskState.ts";

test("cleanup updates do not restart the safe-stop grace period", () => {
  const now = 1800000000000;
  const task = { status: "stopping", stop_requested_at_ns: (now - 61000) * 1e6, updated_at_ns: now * 1e6 };
  assert.equal(forceStopReady(task, now), true);
  assert.equal(forceStopReady({ ...task, stop_requested_at_ns: (now - 59999) * 1e6 }, now), false);
  assert.equal(forceStopReady({ ...task, status: "running" }, now), false);
});

test("old task records retain the original timestamp fallback", () => {
  const now = 1800000000000;
  assert.equal(forceStopReady({ status: "stopping", updated_at_ns: (now - 60000) * 1e6 }, now), true);
  assert.equal(forceStopReady(undefined, now), false);
});
