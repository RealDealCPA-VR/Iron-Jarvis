/**
 * v1.398.0 review (night shift N1) — Coding sessions › Tonight.
 *
 *  - a task whose commands changed the user's MAIN folder says so OPEN on its
 *    row (never folded under "What happened", never by colour alone: an icon
 *    and the words).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, list: null as unknown };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    if (p === "/build/night") return Promise.resolve(api.list);
    return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
  },
  post: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({ ok: true }),
}));
vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ models: [], usable: [], data: { models: [] }, loading: false, error: null, reload: () => {} }),
}));

import { TonightList } from "@/components/harness/TonightList";
import { decodeTask, mainChangedWords } from "@/lib/nightShift";

const OUTSIDE =
  "YOUR MAIN FOLDER CHANGED while this task ran (C:\\work\\shop): its files changed (new, changed or deleted files). " +
  "A night task works on its own branch, but its commands are not confined to it, so look at your main folder before you rely on it.";

function task(id: string, over: Record<string, unknown> = {}) {
  return {
    id, folder: "C:\\work\\shop", words: `Task ${id}`, provider: "", model: "", team: false, try_on: [],
    position: 1, state: "needs_you", thread_id: `s-${id}`, turn_id: "", branch: "ij/x-1a2b", notes: [],
    main_changed: false, created_at: null, updated_at: null, started_at: null, ended_at: null, ...over,
  };
}

function listOf(tasks: unknown[]) {
  return {
    tasks,
    window: { start: "23:00", end: "07:00", open: false, words: "Starts at 11:00 PM tonight.", next_start: null },
    limits: { at_once: 2, task_minutes: 90, card_wait_minutes: 15 },
    emergency_stop: false,
  };
}

beforeEach(() => {
  api.list = listOf([]);
});
afterEach(() => cleanup());

describe("Tonight (review)", () => {
  it("shows a changed main folder OPEN on the row, with an icon and the daemon's words", async () => {
    api.list = listOf([
      task("t1", {
        main_changed: true,
        notes: [
          { kind: "declined", text: "Needs your OK: run a command (nobody answered in 15 minutes, so it was skipped).", at: null },
          { kind: "outside", text: OUTSIDE, at: null },
        ],
      }),
      task("t2", { state: "done", notes: [{ kind: "info", text: "Its commands ran as you.", at: null }] }),
    ]);
    render(<TonightList folders={[]} onOpenSession={() => {}} />);
    const line = await screen.findByTestId("tonight-main-changed-t1");
    expect(line.textContent).toBe(OUTSIDE);
    expect(line.closest("details")).toBeNull();
    expect(line.querySelector("svg")).not.toBeNull();
    expect(within(screen.getByTestId("tonight-task-t2")).queryByTestId("tonight-main-changed-t2")).toBeNull();
  });

  it("decodes main_changed strictly and words it from the outside note", () => {
    expect(decodeTask(task("a", { main_changed: "yes" }))?.mainChanged).toBe(false);
    const t = decodeTask(task("b", { main_changed: true, notes: [{ kind: "outside", text: OUTSIDE, at: null }] }));
    expect(t?.mainChanged).toBe(true);
    expect(mainChangedWords(t!)).toBe(OUTSIDE);
    expect(mainChangedWords({ mainChanged: true, notes: [] })).toBe("Your main folder changed while this task ran.");
  });
});
