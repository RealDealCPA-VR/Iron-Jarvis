/**
 * v1.423.0 (Wave 3 lane E, Team notes Phase 3): DOES A WORKING HABIT HELP —
 * the dashboard half.
 *
 * Pinned here, on the REAL views the daemon sends (the fixture is written by
 * tests/test_team_measure_v14230.py from agents/team_measure — regenerate
 * with IJ_REGEN_HABIT_MEASURE=1):
 *  - decodeMeasure reads a habit's `measure` defensively: missing or junk →
 *    no measure (never zeros), `worse` only when the daemon says exactly true
 *    and both sides have enough work;
 *  - measureWords: ONE quiet line once both sides have 5 pieces ("Since this
 *    habit: first-pass approval 2 of 6 → 5 of 7 · …"), nothing under that;
 *  - Settings › Model team draws that line under the habit, and for a worse
 *    habit the AMBER line "This habit may be making things worse — consider
 *    removing it" beside the habit's own two-press Remove — and nothing is
 *    posted until the owner presses it twice (never removed by itself).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import fixture from "./fixtures/habit-measure-v1423-0.json";

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as string[],
  reloads: 0,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {
      hooks.reloads += 1;
    },
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {
      hooks.reloads += 1;
    },
  }),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string) => {
      hooks.posts.push(path);
      return Promise.resolve({});
    },
    patch: () => Promise.resolve({}),
  };
});

import { ModelTeam } from "@/components/settings/ModelTeam";
import { HABIT_WORSE_WORDS, decodeMeasure, decodeNotes, measureWords } from "@/lib/modelTeam";

const BETTER_LINE =
  "Since this habit: first-pass approval 2 of 6 → 5 of 7 · confirmed findings per piece 0.5 → 0.14 · " +
  "checks passed 3 of 5 → 6 of 6";

function habit(id: string, measure?: unknown): Record<string, unknown> {
  return {
    id,
    text: `Habit ${id}: run the module's tests before reporting a piece as done`,
    findings: 2,
    accepted_at: "2026-10-20T10:00:00",
    ...(measure === undefined ? {} : { measure }),
  };
}

function member(name: string, accepted: unknown[]): Record<string, unknown> {
  return {
    name,
    roster_name: `custom:${name}`,
    description: "",
    provider: "ollama",
    model: "qwen3:8b",
    place: "this_pc",
    cost: "free",
    roles: ["code"],
    account: "",
    account_name: "",
    vendor: "",
    usage: null,
    available: true,
    paused: false,
    notes: { accepted, suggested: 0, hand_edited: false },
  };
}

beforeEach(() => {
  hooks.api = {};
  hooks.posts = [];
  hooks.reloads = 0;
});
afterEach(() => cleanup());

/* -------------------------------------------------------------- decoders --- */

describe("a habit's measure, decoded", () => {
  it("reads the daemon's real views", () => {
    const better = decodeMeasure(fixture.better);
    expect(better?.worse).toBe(false);
    expect(better?.before.firstPass).toEqual({ approved: 2, of: 6 });
    expect(better?.after.checks).toEqual({ passed: 6, of: 6 });
    expect(measureWords(better)).toBe(BETTER_LINE);
    const worse = decodeMeasure(fixture.worse);
    expect(worse?.worse).toBe(true);
    expect(measureWords(worse)).toBe(
      "Since this habit: first-pass approval 5 of 6 → 2 of 6 · confirmed findings per piece 0.17 → 0.17",
    );
  });

  it("says no number when a side has under five pieces", () => {
    const thin = decodeMeasure(fixture.not_enough);
    expect(thin?.after.enough).toBe(false);
    expect(thin?.after.firstPass).toBeNull();
    expect(measureWords(thin)).toBe("");
    // Numbers smuggled beside enough:false are ignored; worse needs enough.
    const forged = decodeMeasure({
      before: { enough: true, pieces: 6, first_pass: { approved: 6, of: 6 }, confirmed: 0 },
      after: { enough: false, pieces: 2, first_pass: { approved: 0, of: 2 } },
      worse: true,
    });
    expect(forged?.worse).toBe(false);
    expect(forged?.after.firstPass).toBeNull();
    expect(measureWords(forged)).toBe("");
    // The words keep the rule on their own too (a measure built elsewhere).
    const side = { enough: true, pieces: 6, firstPass: { approved: 2, of: 6 }, confirmed: 1, confirmedPerPiece: 0.17, checks: null };
    expect(measureWords({ before: side, after: { ...side, enough: false, pieces: 4 }, worse: false })).toBe("");
    expect(measureWords({ before: side, after: side, worse: false })).toContain("first-pass approval 2 of 6 → 2 of 6");
  });

  it("is defensive: missing or junk is NO measure, never zeros", () => {
    expect(decodeMeasure(undefined)).toBeNull();
    expect(decodeMeasure({ before: "x", after: fixture.better.after, worse: false })).toBeNull();
    expect(decodeMeasure({ ...fixture.better, worse: "yes" })?.worse).toBe(false);
    const notes = decodeNotes({ accepted: [habit("a"), habit("b", fixture.better)] });
    expect("measure" in (notes?.accepted[0] ?? {})).toBe(false);
    expect(notes?.accepted[1].measure?.after.firstPass).toEqual({ approved: 5, of: 7 });
  });
});

/* ------------------------------------------------------- the member card --- */

describe("Settings › Model team: does a habit help?", () => {
  it("one quiet line once both sides have enough work, nothing otherwise", () => {
    hooks.api["/team"] = {
      roles: [],
      members: [member("qwen", [habit("h1", fixture.better), habit("h2", fixture.not_enough), habit("h3")])],
    };
    render(<ModelTeam />);
    expect(screen.getByTestId("team-note-measure-h1").textContent).toBe(BETTER_LINE);
    expect(screen.queryByTestId("team-note-worse-h1")).toBeNull();
    // Under five pieces, or a measure that could not be read: no line.
    expect(screen.queryByTestId("team-note-measure-h2")).toBeNull();
    expect(screen.queryByTestId("team-note-measure-h3")).toBeNull();
    expect(screen.getByTestId("team-note-h3").textContent).not.toMatch(/\d of \d/);
  });

  it("an amber line for a worse habit, beside its own two-press Remove — never removed by itself", async () => {
    hooks.api["/team"] = { roles: [], members: [member("qwen", [habit("w1", fixture.worse)])] };
    render(<ModelTeam />);
    const row = screen.getByTestId("team-note-w1");
    const amber = within(row).getByTestId("team-note-worse-w1");
    expect(amber.textContent).toBe(HABIT_WORSE_WORDS);
    expect(amber.textContent).toBe("This habit may be making things worse — consider removing it");
    expect(amber.className).toContain("text-tone-warn");
    // Shown, never acted on: nothing was posted by drawing the card.
    expect(hooks.posts).toEqual([]);
    fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
    expect(hooks.posts).toEqual([]);
    fireEvent.click(within(row).getByRole("button", { name: "Remove?" }));
    await waitFor(() => expect(hooks.posts).toEqual(["/team/notes/w1/remove"]));
  });
});
