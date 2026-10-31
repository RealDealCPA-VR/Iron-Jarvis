/**
 * v1.414.0 review — Build › Code's right column. Each case was RED on the
 * first cut (8a7379ff):
 *
 * - a process listing the daemon could not read (`programs_known: false`)
 *   read "Nothing is running under Claude Code right now";
 * - a listing cut at its bound (`programs_more`) read as the whole list;
 * - Iron-Proxy's numbers had no age ("From Iron-Proxy's last look" — a look
 *   that can be hours old);
 * - a pane's 5-second read started again while the last one was still out,
 *   so a slow git read (a big repository) piled up reads on the daemon.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  calls: [] as string[],
  hold: null as null | { path: string; release: () => void; pending: number },
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      H.calls.push(path);
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      if (H.hold && H.hold.path === path) {
        const hold = H.hold;
        hold.pending += 1;
        return new Promise((resolve) => {
          const prev = hold.release;
          hold.release = () => {
            prev();
            resolve(r);
          };
        });
      }
      return Promise.resolve(r);
    },
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    ijToken: () => "",
    API_BASE: "http://127.0.0.1:8787",
  };
});

vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { CODE_PANES_POLL_MS, CodePanes } from "@/components/buildCode/CodePanes";
import { decodeCodeSession, decodeCodingTools } from "@/lib/buildApp";

const SESSION = decodeCodeSession({
  id: "app_1",
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: "Fix the cart",
  account: "p1",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
  capabilities: { files: false, shell: false, browser: false, extensions: false, memory: false },
})!;
const TOOLS = decodeCodingTools({
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      own_sign_in: false,
      modes: [{ id: "ask", label: "Ask first", dangerous: false, about: "" }],
      hidden_modes: [],
      models: [],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
});

const soon = new Date(Date.now() + 2 * 3600_000).toISOString();
const twoHoursAgo = new Date(Date.now() - 2 * 3600_000).toISOString();
const OVERVIEW = {
  folder: "C:\\work\\shop",
  folder_exists: true,
  on_share: false,
  git: { repo: true, branch: "main", detached: false },
  rules: [],
  context: null,
  tool_running: true,
  programs: [] as unknown[],
  programs_known: true,
  programs_more: false,
};
const CHANGES = {
  source: "git",
  words: "w",
  changes: [],
  files: 0,
  truncated_files: false,
  by_tool: 0,
  no_undo: "",
};
const USAGE = {
  harness: "claude-code",
  account: {
    id: "p1",
    name: "Work",
    plan_label: "Max",
    status: "usage",
    windows: [{ label: "5-hour window", percent: 63, resets_at: soon, at: twoHoursAgo }],
    at: twoHoursAgo,
  },
  seen: null,
  totals: { replies: 0, input: 0, output: 0, cache_read: 0, cost_usd: null, list_price: false, cost_reported_by: "" },
  context: null,
};

function setGets(extra: Record<string, unknown> = {}) {
  H.gets = {
    "/build/app/sessions/app_1/changes": CHANGES,
    "/build/app/sessions/app_1/usage": USAGE,
    "/build/app/sessions/app_1/overview": OVERVIEW,
    "/editor/options": { editors: [], preferred: "" },
    ...extra,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  H.calls = [];
  H.hold = null;
  setGets();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function session(running = true) {
  render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={running} />);
  fireEvent.click(screen.getByRole("tab", { name: "Session" }));
}

describe("Background never claims nothing when it could not look", () => {
  it("an unreadable listing says so, never 'Nothing is running'", async () => {
    setGets({ "/build/app/sessions/app_1/overview": { ...OVERVIEW, programs_known: false } });
    session();
    const note = await screen.findByTestId("code-session-programs-unknown");
    expect(note.textContent).toContain("could not be read");
    expect(screen.queryByTestId("code-session-programs-none")).toBeNull();
    expect(screen.getByTestId("code-pane-session").textContent).not.toMatch(/Nothing is running/);
  });

  it("a listing cut at its bound says more run than are listed", async () => {
    setGets({
      "/build/app/sessions/app_1/overview": {
        ...OVERVIEW,
        programs: [{ pid: 7, name: "node.exe", command: "node a.js" }],
        programs_more: true,
      },
    });
    session();
    await screen.findByTestId("code-session-programs");
    expect(screen.getByTestId("code-session-programs-more").textContent).toMatch(/more run than are listed/i);
  });

  it("an older daemon's answer (no programs_known) still reads as before", async () => {
    const { programs_known: _k, programs_more: _m, ...old } = OVERVIEW;
    setGets({ "/build/app/sessions/app_1/overview": old });
    session();
    expect((await screen.findByTestId("code-session-programs-none")).textContent).toContain("Nothing is running");
  });
});

describe("Iron-Proxy's numbers carry their age", () => {
  it("says how long ago Iron-Proxy saw them", async () => {
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    fireEvent.click(screen.getByRole("tab", { name: "Usage" }));
    await screen.findByTestId("code-usage-proxy");
    expect(screen.getByTestId("code-usage-proxy-when").textContent).toBe(
      "From Iron-Proxy's last look at this account, 2 h ago.",
    );
  });
});

describe("a pane's read never piles up", () => {
  it("starts no new read while the last one is still out", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    H.hold = { path: "/build/app/sessions/app_1/changes", release: () => {}, pending: 0 };
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running />);
    await waitFor(() => expect(H.hold!.pending).toBe(1));
    await act(async () => {
      vi.advanceTimersByTime(CODE_PANES_POLL_MS * 4 + 50);
    });
    expect(H.calls.filter((p) => p.endsWith("/changes")).length).toBe(1);
    // Once it answers, the next tick reads again.
    const hold = H.hold!;
    H.hold = null;
    await act(async () => {
      hold.release();
    });
    await act(async () => {
      vi.advanceTimersByTime(CODE_PANES_POLL_MS + 50);
    });
    await waitFor(() => expect(H.calls.filter((p) => p.endsWith("/changes")).length).toBe(2));
  });
});
