/**
 * v1.439.0 review of chat-first Wave 6, group D (each pin red on the
 * builder's commit d43ab93f):
 *
 *  R1. A MERGED session's pull-request watch is over: the daemon's pass skips
 *      any session whose facts carry `merged_at` (routes/ci_watch.py `_tend`),
 *      so the record stays frozen at its last word ("watching…", "starts a
 *      fix when this session is free"). The Branch view read it and drew that
 *      frozen promise. Now no read, no timer, no line.
 *  R2. No pull request (or a merged one): no CI timer fires a read — the
 *      builder pinned the first read only, never the 60 s clock.
 *  R3. Sign out's tooltip says that a terminal or coding session running on
 *      that account now may stop answering (Iron-Proxy runs the CLI's own
 *      logout in the account's folder, which those sessions read too).
 *  R4. The decoders are fed what the REAL daemon answers
 *      (fixtures/wave6-reconnect-v1439.json, written by
 *      tests/test_wave6_reconnect_review_v14390.py through create_app).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    calls: [] as { method: string; path: string; body?: unknown }[],
    responses: {} as Record<string, unknown>,
    sends: {} as Record<string, unknown>,
  };
});

/** Every useVisibleInterval the components ask for: its callback, period and switch. */
const timers = vi.hoisted(() => ({ list: [] as { fn: () => void; ms: number; enabled: boolean }[] }));

vi.mock("@/lib/useVisibleInterval", () => ({
  useVisibleInterval: (fn: () => void, ms: number, enabled = true) => {
    timers.list.push({ fn, ms, enabled });
  },
}));

vi.mock("@/lib/api", () => {
  const send = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const key = `${method} ${path}`;
    return Promise.resolve(key in api.sends ? api.sends[key] : {});
  };
  return {
    ApiError: api.FakeApiError,
    API_BASE: "http://api.test",
    ijToken: () => "tok",
    wsUrl: (p: string) => `ws://api.test${p}`,
    api: (path: string, init?: { method?: string; body?: string }) =>
      send(init?.method ?? "GET", path, typeof init?.body === "string" ? JSON.parse(init.body) : undefined),
    get: (path: string) => {
      api.calls.push({ method: "GET", path });
      if (path in api.responses) return Promise.resolve(api.responses[path]);
      return Promise.reject(new api.FakeApiError("Not Found", 404));
    },
    post: (path: string, body?: unknown) => send("POST", path, body),
    patch: (path: string, body?: unknown) => send("PATCH", path, body),
    put: (path: string, body?: unknown) => send("PUT", path, body),
    del: (path: string) => send("DELETE", path),
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

vi.mock("@/components/harness/ChangesReview", () => ({ ChangesReview: () => null }));

import AutonomyPage from "@/app/autonomy/page";
import ReflexPage from "@/app/reflex/page";
import { signOutTitle } from "@/components/connections/IronProxyCard";
import { GlobalHotkeys, UNREAD_HOTKEYS_WORDS } from "@/components/help/GlobalHotkeys";
import { BranchView } from "@/components/harness/HarnessBranch";
import { ciWatchWords, decodeCiView } from "@/lib/ciWatch";
import type { SessionGit } from "@/lib/buildGit";

const LONG = { timeout: 10_000 };

const FIXTURE = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "wave6-reconnect-v1439.json"), "utf8"),
) as Record<string, unknown>;

afterEach(() => {
  cleanup();
  api.calls = [];
  api.responses = {};
  api.sends = {};
  timers.list = [];
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

const ciReads = () => api.calls.filter((c) => c.method === "GET" && c.path === "/build/sessions/t1/ci");

const GIT: SessionGit = {
  branch: "ij/fix-cart",
  base: "main",
  baseCommit: "abc",
  worktree: "C:/w",
  checkout: "C:/r",
  repo: "C:/r",
  merged: false,
  mergedInto: "",
  removed: false,
  prUrl: "https://github.com/o/r/pull/7",
};

function primeBranch() {
  const base = "/build/sessions/t1/git";
  api.responses[base] = { branch: "ij/fix-cart", ahead: 1, uncommitted: 0 };
  api.responses[`${base}/diff`] = { files: [] };
  api.responses[`${base}/pr`] = { number: 7, url: GIT.prUrl, checks: { passed: 0, failed: 1, pending: 0 }, words: "1 failed" };
  api.responses["/build/sessions/t1/ci"] = {
    watch: { state: "watching", rounds: 1, max_rounds: 3, failed: [], words: "", next_check_at: null },
    on: true,
  };
}

/** Fire every timer that is switched on, as the 60 s clock would. */
function tickEnabledTimers() {
  for (const t of timers.list) if (t.enabled) t.fn();
}

describe("R1. a merged session's frozen watch is never read or said", () => {
  it("a merged session reads no watch and draws no line", async () => {
    primeBranch();
    render(<BranchView sessionId="t1" git={{ ...GIT, merged: true, mergedInto: "main" }} refreshKey={0} />);
    await waitFor(
      () => expect(api.calls.some((c) => c.path === "/build/sessions/t1/git")).toBe(true),
      LONG,
    );
    // Let any first read land.
    await new Promise((r) => setTimeout(r, 50));
    expect(ciReads()).toHaveLength(0);
    expect(screen.queryByTestId("branch-ci-watch")).toBeNull();
  });

  it("control: the same open, unmerged pull request does read and say it", async () => {
    primeBranch();
    render(<BranchView sessionId="t1" git={GIT} refreshKey={0} />);
    const line = await screen.findByTestId("branch-ci-watch", undefined, LONG);
    expect(line.textContent).toContain("Iron Jarvis is watching its checks.");
  });
});

describe("R2. the CI clock runs only for an open, unmerged pull request", () => {
  it("no pull request: no timer that fires reads the watch", async () => {
    primeBranch();
    render(<BranchView sessionId="t1" git={{ ...GIT, prUrl: "" }} refreshKey={0} />);
    await waitFor(
      () => expect(api.calls.some((c) => c.path === "/build/sessions/t1/git")).toBe(true),
      LONG,
    );
    tickEnabledTimers();
    await new Promise((r) => setTimeout(r, 50));
    expect(ciReads()).toHaveLength(0);
  });

  it("merged: no timer that fires reads the watch", async () => {
    primeBranch();
    render(<BranchView sessionId="t1" git={{ ...GIT, merged: true }} refreshKey={0} />);
    await waitFor(
      () => expect(api.calls.some((c) => c.path === "/build/sessions/t1/git")).toBe(true),
      LONG,
    );
    tickEnabledTimers();
    await new Promise((r) => setTimeout(r, 50));
    expect(ciReads()).toHaveLength(0);
  });

  it("control: an open pull request's clock does read the watch again", async () => {
    primeBranch();
    render(<BranchView sessionId="t1" git={GIT} refreshKey={0} />);
    await screen.findByTestId("branch-ci-watch", undefined, LONG);
    const before = ciReads().length;
    tickEnabledTimers();
    await waitFor(() => expect(ciReads().length).toBeGreaterThan(before), LONG);
  });
});

describe("R3. Sign out says what it does to work already running on the account", () => {
  it("warns that a terminal or coding session on the account may stop answering", () => {
    for (const adopted of [false, true]) {
      expect(signOutTitle({ home_adopted: adopted })).toContain(
        "A terminal or coding session running on this account now may stop being able to answer.",
      );
    }
  });
});

describe("R4. the decoders read what the real daemon answers", () => {
  it("the CI watch: a naive-UTC next look with microseconds is read as UTC", () => {
    const view = decodeCiView(FIXTURE.ci_watching);
    expect(view.on).toBe(true);
    expect(view.watch?.state).toBe("watching");
    expect(view.watch?.rounds).toBe(1);
    expect(view.watch?.maxRounds).toBe(3);
    expect(view.watch?.failed).toEqual(["tests"]);
    expect(view.watch?.nextCheckAt?.toISOString()).toBe("2026-11-06T18:16:58.831Z");
    const words = ciWatchWords(view, new Date("2026-11-06T18:00:00Z"));
    expect(words).toContain("Next look at");
    expect(words).toContain("2 fix rounds left");
    expect(decodeCiView(FIXTURE.ci_none).watch).toBeNull();
  });

  it("the Reflex page reads the nested answer with a saved link, and the removal answer", async () => {
    api.responses["/triggers"] = FIXTURE.triggers_saved_link;
    api.sends["DELETE /triggers/calendar/url"] = FIXTURE.calendar_url_removed;
    render(<ReflexPage />);
    const slot = await screen.findByTestId("calendar-url-remove", undefined, LONG);
    fireEvent.click(within(slot).getByRole("button", { name: "Remove" }));
    fireEvent.click(within(slot).getByRole("button", { name: "Remove the link?" }));
    expect(await screen.findByText(/The saved calendar link was removed\./, undefined, LONG)).toBeInTheDocument();
  });

  it("a second removal says there was nothing to remove", async () => {
    api.responses["/triggers"] = FIXTURE.triggers_saved_link;
    api.sends["DELETE /triggers/calendar/url"] = FIXTURE.calendar_url_none;
    render(<ReflexPage />);
    const slot = await screen.findByTestId("calendar-url-remove", undefined, LONG);
    fireEvent.click(within(slot).getByRole("button", { name: "Remove" }));
    fireEvent.click(within(slot).getByRole("button", { name: "Remove the link?" }));
    expect(await screen.findByText("There was no saved calendar link to remove.", undefined, LONG)).toBeInTheDocument();
  });

  it("the Autonomy page counts the grants the real DELETE /goals/{id} revoked", async () => {
    api.responses["/goals"] = {
      goals: [
        {
          id: "GOAL_ID",
          name: "inbox zero",
          state: "active",
          contract_text: "Create a file summarizing it.",
          schedule: "",
          budget: { max_tokens: 1000 },
          spent: { tokens: 0, dollars: 0, wallclock_s: 0, iterations: 0 },
          last_run_at: null,
          project_id: null,
          verifier: { kind: "manual", checks: [] },
        },
      ],
    };
    api.responses["/goals/digest?hours=24"] = { digest: { goals: [], since: "2026-11-05T09:00:00Z" } };
    api.sends["DELETE /goals/GOAL_ID"] = FIXTURE.goal_delete;
    render(<AutonomyPage />);
    const slot = await screen.findByTestId("goal-delete-GOAL_ID", undefined, LONG);
    fireEvent.click(within(slot).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(slot).getByRole("button", { name: "Delete it?" }));
    expect(
      await screen.findByText(
        '"inbox zero" deleted, and 1 action it could take without asking was revoked.',
        undefined,
        LONG,
      ),
    ).toBeInTheDocument();
  });
});

describe("R5. an older desktop app with no shell bridge is still the desktop app", () => {
  it("says it was not told which keys it holds, never that this is a browser tab", async () => {
    (window as unknown as { ironjarvis: unknown }).ironjarvis = { isDesktop: true };
    render(<GlobalHotkeys />);
    expect((await screen.findByTestId("help-hotkeys-none", undefined, LONG)).textContent).toBe(UNREAD_HOTKEYS_WORDS);
  });
});
