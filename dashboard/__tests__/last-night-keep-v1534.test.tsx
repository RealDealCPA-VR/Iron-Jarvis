/**
 * Fourth review C-CS-2: KEEP AN ATTEMPT STRAIGHT FROM LAST NIGHT. A night task
 * tried on several models used to say "Choose one: open the session and keep
 * the one you want" — the user opened the session, found the card and pressed
 * Keep there. Now its Last night row lists each attempt's ending (the
 * session's own saved card, read from the session) with Keep on each one the
 * card itself would offer Keep on: one that finished, or one that changed
 * files before it ended (a cut attempt whose files are good, too). The press
 * goes through the existing keep route (POST /build/sessions/{id}/attempts/
 * keep); nothing is kept without it, even when only one attempt finished.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    summaries: null as unknown,
    session: null as unknown,
    calls: [] as [string, string, unknown][],
    fail: {} as Record<string, Error>,
  };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.calls.push(["GET", p, undefined]);
    if (api.fail[p]) return Promise.reject(api.fail[p]);
    if (p === "/build/night/summaries") return Promise.resolve(api.summaries);
    if (p === "/build/sessions/bs_try") return Promise.resolve(api.session);
    return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
  },
  post: (p: string, b?: unknown) => {
    api.calls.push(["POST", p, b]);
    if (api.fail[p]) return Promise.reject(api.fail[p]);
    if (p === "/build/sessions/bs_try/attempts/keep") {
      // The daemon kept it: the card now names the kept attempt.
      const s = api.session as { messages: { attempts?: { kept?: string; merged_into?: string } }[] };
      const card = s.messages[1].attempts!;
      card.kept = (b as { attempt: string }).attempt;
      card.merged_into = "ij/night-shop-1a2b";
      return Promise.resolve({ words: "Kept Codex's attempt: merged into ij/night-shop-1a2b.", note: "" });
    }
    return Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({ ok: true }),
}));

import { LastNightList } from "@/components/harness/LastNightList";

const git = (n: string) => ({ worktree: `C:\\work\\.ij\\${n}`, branch: `ij/try-${n}` });

/** The attempts card as the daemon saves it on the reply line (after CS2: a
 *  cut attempt is "failed" with the cut words, its files still listed). */
function card() {
  return {
    task: "Add a cart total",
    base: "ij/night-shop-1a2b",
    items: [
      { id: "a1", label: "Claude", state: "done", why: "finished", words: "Finished", files: ["cart.py"], git: git("a1") },
      {
        id: "a2",
        label: "Codex",
        state: "failed",
        why: "failed",
        words: "Failed: its answer was cut off at the model's output limit, so it did not finish",
        files: ["cart.py", "total.py"],
        git: git("a2"),
      },
      { id: "a3", label: "Local model", state: "failed", why: "failed", words: "Failed: the model's server went away", files: [], git: git("a3") },
    ],
  };
}

function session(over: Record<string, unknown> = {}) {
  return {
    id: "bs_try",
    title: "Add a cart total",
    updated_at: "2026-10-27T06:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    mode: "act",
    setup: {},
    messages: [
      { role: "user", content: "Add a cart total", turnId: "t9" },
      { role: "assistant", content: "Tried on 3 models.", turnId: "t9", attempts: card() },
    ],
    queued: [],
    turn_id: "",
    ...over,
  };
}

function summaries(row: Record<string, unknown> = {}) {
  return {
    nights: [
      {
        night: "2026-10-26",
        label: "Monday, October 26",
        counts: { needs_you: 1 },
        rows: [
          {
            id: "night_try",
            thread_id: "bs_try",
            title: "Add a cart total",
            folder: "C:\\work\\shop",
            folder_name: "shop",
            branch: "ij/night-shop-1a2b",
            state: "needs_you",
            needs: ["Choose one: open the session and keep the one you want"],
            try_on: 3,
            session: { exists: true, archived: false, has_branch: true },
            ...row,
          },
        ],
      },
    ],
  };
}

beforeEach(() => {
  api.calls = [];
  api.fail = {};
  api.summaries = summaries();
  api.session = session();
});
afterEach(() => cleanup());

const keeps = () => api.calls.filter(([m, p]) => m === "POST" && p.endsWith("/attempts/keep"));

async function shown() {
  render(<LastNightList onOpenSession={vi.fn()} />);
  return screen.findByTestId("last-night-attempts-night_try");
}

describe("Last night: keep an attempt from the row", () => {
  it("lists each attempt's ending, and offers Keep where the card would: finished, or cut with files", async () => {
    const list = await shown();
    expect(within(list).getByTestId("last-night-attempt-night_try-a1").textContent).toContain("Claude: Finished");
    expect(within(list).getByTestId("last-night-attempt-night_try-a2").textContent).toContain(
      "Codex: Failed: its answer was cut off at the model's output limit, so it did not finish",
    );
    expect(within(list).getByTestId("last-night-keep-night_try-a1")).toHaveTextContent("Keep");
    // A cut attempt whose files are good: its work is there to keep.
    expect(within(list).getByTestId("last-night-keep-night_try-a2")).toHaveTextContent("Keep");
    // A failed attempt that changed nothing: nothing to keep.
    expect(within(list).queryByTestId("last-night-keep-night_try-a3")).toBeNull();
    // Nothing was kept by showing the row.
    await new Promise((r) => setTimeout(r, 30));
    expect(keeps()).toEqual([]);
  });

  it("the press keeps through the existing route with the right ids, and says the daemon's words", async () => {
    const list = await shown();
    fireEvent.click(within(list).getByTestId("last-night-keep-night_try-a2"));
    await waitFor(() =>
      expect(keeps()).toEqual([["POST", "/build/sessions/bs_try/attempts/keep", { turn_id: "t9", attempt: "a2" }]]),
    );
    expect((await screen.findByTestId("last-night-said-night_try")).textContent).toBe(
      "Kept Codex's attempt: merged into ij/night-shop-1a2b.",
    );
    // The card is read again: Keep is gone, the kept one says so.
    await waitFor(() => expect(screen.queryByTestId("last-night-keep-night_try-a1")).toBeNull());
    expect(screen.getByTestId("last-night-attempt-night_try-a2").textContent).toContain("Kept: merged into ij/night-shop-1a2b");
  });

  it("a refused keep says the daemon's words and keeps nothing on the screen", async () => {
    api.fail["/build/sessions/bs_try/attempts/keep"] = new Error("Something is still running in this session. Wait for it to finish.");
    const list = await shown();
    fireEvent.click(within(list).getByTestId("last-night-keep-night_try-a1"));
    expect((await screen.findByTestId("last-night-problem-night_try")).textContent).toBe(
      "Something is still running in this session. Wait for it to finish.",
    );
    expect(screen.getByTestId("last-night-keep-night_try-a1")).toBeInTheDocument();
  });

  it("no Keep while the session runs or is archived (the route would refuse it); the endings still show", async () => {
    api.session = session({ running: true });
    let list = await shown();
    expect(within(list).getByTestId("last-night-attempt-night_try-a1")).toBeInTheDocument();
    expect(within(list).queryByRole("button")).toBeNull();
    cleanup();
    api.session = session({ archived: true });
    list = await shown();
    expect(within(list).queryByRole("button")).toBeNull();
  });

  it("a task that was not tried on several models reads no session", async () => {
    api.summaries = summaries({ try_on: 0 });
    render(<LastNightList onOpenSession={vi.fn()} />);
    await screen.findByTestId("last-night-task-night_try");
    await new Promise((r) => setTimeout(r, 30));
    expect(api.calls.some(([, p]) => p === "/build/sessions/bs_try")).toBe(false);
    expect(screen.queryByTestId("last-night-attempts-night_try")).toBeNull();
  });
});
