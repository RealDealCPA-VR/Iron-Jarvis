/**
 * 25-a (R3.5) — Last night › MERGE ALL THAT PASSED
 * (components/harness/LastNightMerge, lib/nightLand; the daemon's
 * harness/landing.py and routes/night_land.py).
 *
 *  - the head line starts as the summary's own numbers ("5 of 6 passed their
 *    tests.") and ONE main button; the preview is read ONCE when Last night
 *    opens (never on the summary's beat), and then the button's count is the
 *    card's and a clashing row says so on the row, before any press;
 *  - the first press reads the preview and shows EVERY file of every branch
 *    with its lines, the branch it goes into, the tests run after each, a
 *    clash in words, and each row left out with why;
 *  - the second press sends exactly the preview's id;
 *  - a running merge is read again until it ends; the result line is the
 *    record's own words with each step's time, and Undo is the plan's (two
 *    presses, its `how`); a merge Undo reversed says so on its row;
 *  - the words are VOCABULARY.md's: Merge all, Merged, clash — never
 *    "Land all green", "Landed" or "conflict".
 */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    calls: [] as [string, string, unknown][],
    merge: null as unknown,
    rows: [] as unknown[],
    afterPost: null as unknown,
    record: null as unknown,
    fail: {} as Record<string, Error>,
  };
});

const NIGHT = "2026-11-21";

function summaries() {
  return {
    nights: [
      {
        night: NIGHT,
        label: "Saturday, November 21",
        rows: api.rows,
        counts: { failed: 1, needs_you: 0, running: 0, done: 5, stopped: 0 },
        words: { title: "Last night: 1 failed, 5 done", body: "" },
        land: {
          folders: [
            {
              folder: "D:\\code\\shop", folder_key: "d:\\code\\shop", folder_name: "shop", green: 5, total: 6,
              words: "5 of 6 passed their tests.", button: "Merge all 5",
            },
          ],
          green: 5,
          total: 6,
          merges: api.merge ? [api.merge] : [],
        },
      },
    ],
    keep: 14,
  };
}

const PREVIEW = {
  night: NIGHT,
  emergency_stop: false,
  merges: [],
  landings: [
    {
      id: "lp_0123456789abcdef0123",
      folder: "D:\\code\\shop",
      folder_key: "d:\\code\\shop",
      folder_name: "shop",
      base: "main",
      into: "main in D:\\code\\shop",
      command: "npm test",
      command_words: "Tests run after each: `npm test`",
      ready: 1,
      green: 2,
      total: 3,
      blocked: "",
      words: "2 of 3 passed their tests.",
      button: "Merge it",
      steps: [
        {
          task_id: "night_a", thread_id: "bs_a", title: "Add CSV export", branch: "ij/add-csv-1a2b", ready: true,
          commits: [{ sha: "1a2b3c4", subject: "Add CSV export" }], commit_count: 1,
          files: [
            { path: "src/export.ts", status: "created", added: 40, removed: 0 },
            { path: "src/menu.ts", status: "modified", added: 3, removed: 1 },
          ],
          file_count: 2, added: 43, removed: 1, clash: [], clash_words: "",
        },
        {
          task_id: "night_b", thread_id: "bs_b", title: "Rename the cart", branch: "ij/rename-cart-9f9f", ready: false,
          commits: [{ sha: "9f9f000", subject: "Rename the cart" }], commit_count: 1,
          files: [{ path: "src/cart.ts", status: "modified", added: 5, removed: 5 }],
          file_count: 1, added: 5, removed: 5, clash: ["src/cart.ts"], clash_words: "Merging would clash in src/cart.ts.",
        },
      ],
      left: [{ task_id: "night_c", title: "Fix the parser", why: "Its tests failed." }],
      merged: [],
    },
  ],
};

function row(id: string, title: string, extra: Record<string, unknown> = {}) {
  return {
    id, thread_id: `bs_${id}`, title, folder: "D:\\code\\shop", folder_name: "shop", branch: `ij/${id}`, state: "done",
    tests: "passed", files: 1, session: { exists: true, has_branch: true, merged: false, merged_into: "" }, ...extra,
  };
}

const ROWS = [
  row("night_a", "Add CSV export"),
  row("night_b", "Rename the cart"),
  row("night_c", "Fix the parser", { state: "failed", tests: "failed" }),
];

const RUNNING = {
  id: "land_1", night: NIGHT, folder: "D:\\code\\shop", base: "main", into: "main in D:\\code\\shop", state: "running",
  command: "npm test", merged: 0, words: "Merging 1 branch into main…", undone: false, undo_words: "",
  steps: [{ task_id: "night_a", title: "Add CSV export", branch: "ij/add-csv-1a2b", state: "testing", line: "", seconds: null }],
};

const MERGED = {
  ...RUNNING,
  state: "merged",
  merged: 1,
  words: "Merged 1: main moved from a1b2c3d to d4e5f6a. Nothing was pushed: main changed on this PC only.",
  steps: [{ ...RUNNING.steps[0], state: "merged", line: "Checked by Iron Jarvis: 3 tests ran, 3 passed.", seconds: 72.4 }],
  undo: { how: "put_back", button: "Undo: put main back at a1b2c3d", words: "Undo puts main back at a1b2c3d, exactly as it was before." },
};

vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.calls.push(["GET", p, undefined]);
    if (api.fail[p]) return Promise.reject(api.fail[p]);
    if (p === "/build/night/summaries") return Promise.resolve(summaries());
    if (p === `/build/night/summaries/${NIGHT}/land`) return Promise.resolve(PREVIEW);
    if (p === "/build/night/land/land_1") return Promise.resolve(api.record);
    return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
  },
  post: (p: string, b?: unknown) => {
    api.calls.push(["POST", p, b]);
    if (api.fail[p]) return Promise.reject(api.fail[p]);
    if (p === `/build/night/summaries/${NIGHT}/land`) return Promise.resolve({ merge: api.afterPost });
    if (p === "/build/night/land/land_1/undo") {
      return Promise.resolve({ ...MERGED, undone: true, undo_how: "put_back", undo_words: "Undone: main is back at a1b2c3d, exactly as it was. Nothing was pushed." });
    }
    return Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({ ok: true }),
}));

import { LastNightList } from "@/components/harness/LastNightList";
import { RETIRED_ON_SURFACES, isMergeSurface, readableCopy } from "./helpers/retiredWords";
import { stepEndWords } from "@/lib/nightLand";

async function shown() {
  render(<LastNightList onOpenSession={vi.fn()} />);
  return screen.findByTestId(`merge-all-${NIGHT}`);
}

beforeEach(() => {
  api.calls = [];
  api.fail = {};
  api.merge = null;
  api.rows = ROWS;
  api.afterPost = MERGED;
  api.record = MERGED;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Merge all that passed", () => {
  it("the head line follows the preview read once on open, and one main press opens it with every file", async () => {
    const box = await shown();
    // Read ONCE when Last night opens: the button's count is then the card's own
    // (a clash is left out), and so are the numbers.
    await waitFor(() => expect(within(box).getByTestId("merge-open").textContent).toBe("Merge it"));
    expect(within(box).getByTestId("merge-head").textContent).toBe("2 of 3 passed their tests.");
    expect(api.calls.filter(([m, p]) => m === "GET" && p === `/build/night/summaries/${NIGHT}/land`)).toHaveLength(1);
    const open = within(box).getByTestId("merge-open");
    fireEvent.click(open);
    const card = await screen.findByTestId("merge-preview");
    expect(card.textContent).toContain("Into main in D:\\code\\shop");
    expect(within(card).getByTestId("merge-command").textContent).toBe("Tests run after each: `npm test`");
    const a = within(card).getByTestId("merge-files-night_a");
    expect(within(a).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "src/export.ts +40 −0",
      "src/menu.ts +3 −1",
    ]);
    expect(within(card).getByTestId("merge-files-night_b").textContent).toContain("src/cart.ts +5 −5");
    expect(within(card).getByTestId("merge-clash-night_b").textContent).toBe("Merging would clash in src/cart.ts.");
    expect(within(card).getByTestId("merge-step-night_b").dataset.ready).toBe("false");
    expect(within(card).getByTestId("merge-left").textContent).toContain("Fix the parser: Its tests failed.");
    expect(within(card).getByTestId("merge-confirm").textContent).toBe("Merge it");
  });

  it("a clash is on its row before any press, and the summary's beat never reads the preview again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await shown();
    expect((await screen.findByTestId("last-night-clash-night_b")).textContent).toBe("Merging would clash in src/cart.ts.");
    expect(screen.queryByTestId("last-night-clash-night_a")).toBeNull();
    expect(screen.queryByTestId("merge-preview")).toBeNull(); // nothing pressed
    const summaryReads = () => api.calls.filter(([m, p]) => m === "GET" && p === "/build/night/summaries").length;
    const before = summaryReads();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(25_000);
    });
    await waitFor(() => expect(summaryReads()).toBeGreaterThanOrEqual(before + 2));
    expect(api.calls.filter(([m, p]) => m === "GET" && p === `/build/night/summaries/${NIGHT}/land`)).toHaveLength(1);
    expect(screen.getByTestId("last-night-clash-night_b").textContent).toBe("Merging would clash in src/cart.ts.");
  });

  it("nothing that can be merged now: the head offers only why, never a Merge", async () => {
    PREVIEW.landings[0].ready = 0;
    PREVIEW.landings[0].button = "";
    PREVIEW.landings[0].id = "";
    try {
      const box = await shown();
      await waitFor(() => expect(within(box).getByTestId("merge-open").textContent).toBe("Show why"));
      fireEvent.click(within(box).getByTestId("merge-open"));
      const card = await screen.findByTestId("merge-preview");
      expect(within(card).queryByTestId("merge-confirm")).toBeNull();
      expect(within(card).getByTestId("merge-clash-night_b").textContent).toBe("Merging would clash in src/cart.ts.");
    } finally {
      PREVIEW.landings[0].ready = 1;
      PREVIEW.landings[0].button = "Merge it";
      PREVIEW.landings[0].id = "lp_0123456789abcdef0123";
    }
  });

  it("the second press sends exactly the preview's id, and the result line is the record's words", async () => {
    await shown();
    fireEvent.click(screen.getByTestId("merge-open"));
    fireEvent.click(await screen.findByTestId("merge-confirm"));
    await waitFor(() =>
      expect(api.calls.filter(([m, p]) => m === "POST" && p === `/build/night/summaries/${NIGHT}/land`)).toEqual([
        ["POST", `/build/night/summaries/${NIGHT}/land`, { preview_id: "lp_0123456789abcdef0123" }],
      ]),
    );
    const result = await screen.findByTestId("merge-result");
    await waitFor(() => expect(within(result).getByTestId("merge-undo").textContent).toBe("Undo: put main back at a1b2c3d"));
    expect(result.textContent).toContain(
      "Merged 1: main moved from a1b2c3d to d4e5f6a. Nothing was pushed: main changed on this PC only.",
    );
    expect(result.textContent).toContain("Undo puts main back at a1b2c3d, exactly as it was before.");
    // Each step and its time, folded under the result.
    const times = within(result).getByTestId("merge-step-times");
    expect(within(times).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "1. Add CSV export: merged · 1 min 12 s",
    ]);
  });

  it("a merge Undo reversed says so on its row", async () => {
    api.rows = [
      row("night_a", "Add CSV export", {
        session: { exists: true, has_branch: true, merged: true, merged_into: "main", merge_reversed: true },
      }),
    ];
    await shown();
    expect((await screen.findByTestId("last-night-merged-night_a")).textContent).toBe(
      "Merged into main, then reversed by Undo",
    );
  });

  it("a running merge is read again until it ends; Undo is two presses and sends the plan's way", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.afterPost = RUNNING;
    api.record = RUNNING;
    await shown();
    fireEvent.click(screen.getByTestId("merge-open"));
    fireEvent.click(await screen.findByTestId("merge-confirm"));
    const running = await screen.findByTestId("merge-running");
    expect(running.textContent).toBe("Running `npm test` after merging 'Add CSV export' (1 of 1)…");
    api.record = MERGED;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_600);
    });
    const result = await screen.findByTestId("merge-result");
    await waitFor(() => expect(within(result).getByTestId("merge-undo").textContent).toBe("Undo: put main back at a1b2c3d"));
    const undo = within(within(result).getByTestId("merge-undo")).getByRole("button");
    fireEvent.click(undo);
    expect(api.calls.some(([m, p]) => m === "POST" && p.endsWith("/undo"))).toBe(false);
    fireEvent.click(within(within(result).getByTestId("merge-undo")).getByRole("button"));
    await waitFor(() =>
      expect(api.calls.filter(([m, p]) => m === "POST" && p.endsWith("/undo"))).toEqual([
        ["POST", "/build/night/land/land_1/undo", { how: "put_back" }],
      ]),
    );
    expect((await screen.findByTestId("merge-undone")).textContent).toBe(
      "Undone: main is back at a1b2c3d, exactly as it was. Nothing was pushed.",
    );
  });

  it("a refused merge reads the preview again and says why", async () => {
    await shown();
    fireEvent.click(screen.getByTestId("merge-open"));
    api.fail[`/build/night/summaries/${NIGHT}/land`] = new api.ApiError(
      "Something changed since you looked (a branch or main moved), so nothing was merged. Look again.", 409,
    );
    fireEvent.click(await screen.findByTestId("merge-confirm"));
    expect((await screen.findByTestId("merge-problem")).textContent).toContain("Something changed since you looked");
    // The card is read again, so it shows what is true now.
    // (once when Last night opened, once at the first press, once after the refusal)
    await waitFor(() =>
      expect(api.calls.filter(([m, p]) => m === "GET" && p === `/build/night/summaries/${NIGHT}/land`)).toHaveLength(3),
    );
    expect(screen.getByTestId("merge-preview")).toBeTruthy();
  });
});

describe("a step's end", () => {
  it("a step merged only in the copy (the base never moved) never reads plain merged", () => {
    expect(stepEndWords("merged")).toBe("merged");
    expect(stepEndWords("copy_only")).toBe("merged in the copy only");
  });
});

describe("one word per thing", () => {
  it("the merge's screens say Merge all, Merged and clash, never Land all green, Landed or conflict", () => {
    // The scanner reads what a person reads (strings and JSX text, never comments).
    for (const f of ["components/harness/LastNightMerge.tsx", "lib/nightLand.ts", "components/harness/LastNightList.tsx"]) {
      expect(isMergeSurface(f)).toBe(true);
      const prose = readableCopy(f).map((p) => p.text).join("\n");
      expect(prose).not.toMatch(/\bland(?:ed|ing)?\b|\bgreen\b|\bconflicts?\b/i);
    }
    expect(RETIRED_ON_SURFACES.map((r) => r.word)).toEqual(expect.arrayContaining(["Land all green", "conflict"]));
    const vocab = readFileSync(path.join(__dirname, "..", "..", "VOCABULARY.md"), "utf-8");
    expect(vocab).toMatch(/\*\*Merge all that passed\*\*/);
    expect(vocab).toMatch(/\*\*clash\*\*/);
  });
});
