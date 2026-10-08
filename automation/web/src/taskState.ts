import type { Task } from "./types";

// Progress and cleanup logs may update updated_at_ns after stopping begins.
// The grace period belongs to the stop request, with a legacy fallback only.
export function forceStopReady(task: Task | undefined, now = Date.now()): boolean {
  return task?.status === "stopping"
    && now - (task.stop_requested_at_ns || task.updated_at_ns) / 1e6 >= 60000;
}
