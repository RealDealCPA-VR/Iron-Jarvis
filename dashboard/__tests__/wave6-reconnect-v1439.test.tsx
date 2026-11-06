/**
 * v1.439.0 — chat-first Wave 6, group D: four features the daemon had and no
 * screen reached, plus two honesty gaps on Help.
 *
 *  1. A Goal from Chat is deleted from the Autonomy page (DELETE /goals/{id}),
 *     two presses, the daemon's answer said (revoked grants counted).
 *  2. An Iron-Proxy account is SIGNED OUT, not only removed
 *     (POST /iron-proxy/accounts/{id}/signout), two presses, only on a
 *     signed-in subscription account.
 *  3. A coding session's pull-request watch is ONE quiet line under the pull
 *     request (GET /build/sessions/{id}/ci), only when a watch exists.
 *  4. A saved calendar link is removed from the Reflex page
 *     (DELETE /triggers/calendar/url), two presses.
 *  5. Help says when the tray's "Restart Iron Jarvis" appears (only after the
 *     automatic restarts gave up), never as if it were always there.
 *  6. Help shows the global hotkeys the desktop app REALLY registered, and in
 *     a browser says they are the desktop app's.
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
    /** method + path -> answer (POST/DELETE/PATCH). */
    sends: {} as Record<string, unknown>,
    fail: {} as Record<string, { status: number; message: string }>,
  };
});

vi.mock("@/lib/api", () => {
  const send = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const key = `${method} ${path}`;
    const f = api.fail[key];
    if (f) return Promise.reject(new api.FakeApiError(f.message, f.status));
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
      const f = api.fail[`GET ${path}`];
      if (f) return Promise.reject(new api.FakeApiError(f.message, f.status));
      if (path in api.responses) return Promise.resolve(api.responses[path]);
      return Promise.reject(new api.FakeApiError(`Not Found`, 404));
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
import { IronProxyCard, signOutTitle, signedOutWords } from "@/components/connections/IronProxyCard";
import { BranchView } from "@/components/harness/HarnessBranch";
import {
  BROWSER_HOTKEYS_WORDS,
  GlobalHotkeys,
  UNREAD_HOTKEYS_WORDS,
  hotkeyLines,
} from "@/components/help/GlobalHotkeys";
import { ciWatchWords, decodeCiView, utcMoment } from "@/lib/ciWatch";
import { hotkeysOf } from "@/lib/desktopShell";
import type { SessionGit } from "@/lib/buildGit";

const LONG = { timeout: 10_000 };

afterEach(() => {
  cleanup();
  api.calls = [];
  api.responses = {};
  api.sends = {};
  api.fail = {};
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  window.localStorage.clear();
});

/** Press a two-press ConfirmButton: the first press arms it, the second runs. */
async function pressTwice(container: HTMLElement, label: RegExp | string) {
  const first = within(container).getByRole("button", { name: label });
  fireEvent.click(first);
  const armed = within(container).getByRole("button");
  fireEvent.click(armed);
}

const sent = (method: string, path: string) =>
  api.calls.filter((c) => c.method === method && c.path === path);

/* ------------------------------------------------------- 1. goal delete */

function primeAutonomy(goals: unknown[]) {
  api.responses["/goals"] = { goals };
  api.responses["/goals/digest?hours=24"] = { digest: { goals: [], since: "2026-11-05T09:00:00Z" } };
}

function chatGoal(id: string, name: string, state = "active") {
  return {
    id,
    name,
    state,
    contract_text: "Keep the inbox tidy.",
    schedule: "0 9 * * *",
    budget: { max_dollars: 2 },
    spent: { tokens: 0, dollars: 0, wallclock_s: 0, iterations: 0 },
    last_run_at: null,
    project_id: null,
    verifier: { kind: "manual", checks: [] },
  };
}

describe("1. a Goal from Chat can be deleted from the Autonomy page", () => {
  it("is two presses, sends DELETE /goals/{id} only on the second, and says what was revoked", async () => {
    primeAutonomy([chatGoal("g_1", "Inbox shepherd"), chatGoal("g_2", "Old crawler", "stopped")]);
    api.sends["DELETE /goals/g_1"] = { deleted: "g_1", revoked_grants: ["gr_a", "gr_b"] };
    render(<AutonomyPage />);
    const slot = await screen.findByTestId("goal-delete-g_1", undefined, LONG);
    fireEvent.click(within(slot).getByRole("button", { name: "Delete" }));
    expect(sent("DELETE", "/goals/g_1")).toHaveLength(0); // one press arms only
    fireEvent.click(within(slot).getByRole("button", { name: "Delete it?" }));
    await waitFor(() => expect(sent("DELETE", "/goals/g_1")).toHaveLength(1), LONG);
    expect(
      await screen.findByText(
        '"Inbox shepherd" deleted, and 2 actions it could take without asking were revoked.',
        undefined,
        LONG,
      ),
    ).toBeInTheDocument();
    // A goal in a terminal state can be deleted too (every state).
    expect(screen.getByTestId("goal-delete-g_2")).toBeInTheDocument();
  });

  it("shows the daemon's own words when it refuses", async () => {
    primeAutonomy([chatGoal("g_1", "Inbox shepherd")]);
    api.fail["DELETE /goals/g_1"] = { status: 404, message: "goal not found" };
    render(<AutonomyPage />);
    const slot = await screen.findByTestId("goal-delete-g_1", undefined, LONG);
    await pressTwice(slot, "Delete");
    expect(await screen.findByText("goal not found", undefined, LONG)).toBeInTheDocument();
  });
});

/* ----------------------------------------------- 2. Iron-Proxy sign out */

function acct(over: Record<string, unknown>) {
  return {
    id: "a",
    title: "Work Claude",
    provider: "anthropic",
    lane: "cli",
    order: 0,
    enabled: true,
    state: { status: "ready", served: 0 },
    home_adopted: false,
    ...over,
  };
}

function primeProxy(accounts: unknown[]) {
  const snap = {
    status: { enabled: true, running: true, owned: true, url: "http://127.0.0.1:4100", version: "0.6.0", error: null, bundled: true },
    accounts,
    discovered: [],
    providers_used_by_jarvis: { anthropic: "claude-cli", openai: "codex-cli", xai: "grok-cli" },
  };
  api.responses["/iron-proxy"] = snap;
  api.responses["/iron-proxy?discover=0"] = snap;
}

describe("2. an Iron-Proxy account can be signed out, not only removed", () => {
  it("a signed-in CLI account offers Sign out; two presses post the signout route and keep the account", async () => {
    primeProxy([
      acct({ id: "a", title: "Work Claude" }),
      acct({ id: "d", title: "Home Codex", provider: "openai", state: { status: "unauthenticated" } }),
      acct({ id: "k", title: "API key", lane: "api" }),
    ]);
    render(<IronProxyCard />);
    const slot = await screen.findByTestId("iron-proxy-signout-a", undefined, LONG);
    // A signed-out account and an API key offer no Sign out.
    expect(screen.queryByTestId("iron-proxy-signout-d")).toBeNull();
    expect(screen.queryByTestId("iron-proxy-signout-k")).toBeNull();
    fireEvent.click(within(slot).getByRole("button", { name: "Sign out" }));
    expect(sent("POST", "/iron-proxy/accounts/a/signout")).toHaveLength(0);
    fireEvent.click(within(slot).getByRole("button", { name: "Press again to sign out" }));
    await waitFor(() => expect(sent("POST", "/iron-proxy/accounts/a/signout")).toHaveLength(1), LONG);
    expect(await screen.findByText(signedOutWords("Work Claude"), undefined, LONG)).toBeInTheDocument();
    // Never the remove route.
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("says before the press that the account stays, and that the existing login is this PC's own", () => {
    expect(signOutTitle({ home_adopted: false })).toContain("The account stays here and needs a new sign-in");
    expect(signOutTitle({ home_adopted: false })).not.toContain("this PC's own login");
    expect(signOutTitle({ home_adopted: true })).toContain("this PC's own login");
  });

  it("a refused sign-out shows the daemon's words", async () => {
    primeProxy([acct({ id: "a", title: "Work Claude" })]);
    api.fail["POST /iron-proxy/accounts/a/signout"] = { status: 502, message: "Iron-Proxy could not sign it out" };
    render(<IronProxyCard />);
    const slot = await screen.findByTestId("iron-proxy-signout-a", undefined, LONG);
    await pressTwice(slot, "Sign out");
    expect(await screen.findByText(/Iron-Proxy could not sign it out/, undefined, LONG)).toBeInTheDocument();
  });
});

/* --------------------------------------------- 3. the pull-request watch */

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

function primeBranch(ci: unknown) {
  const base = "/build/sessions/t1/git";
  api.responses[base] = { branch: "ij/fix-cart", ahead: 1, uncommitted: 0 };
  api.responses[`${base}/diff`] = { files: [] };
  api.responses[`${base}/pr`] = { number: 7, url: GIT.prUrl, checks: { passed: 0, failed: 1, pending: 0 }, words: "1 failed" };
  if (ci !== undefined) api.responses["/build/sessions/t1/ci"] = ci;
}

describe("3. a coding session's pull-request watch is one quiet line", () => {
  it("shows rounds left and the next look while watching, under the pull request", async () => {
    const next = new Date(Date.now() + 10 * 60_000);
    primeBranch({
      watch: {
        state: "watching",
        rounds: 1,
        max_rounds: 3,
        failed: [],
        words: "",
        // The daemon writes UTC with no zone (SQLite drops tzinfo).
        next_check_at: next.toISOString().replace("Z", ""),
      },
      on: true,
    });
    render(<BranchView sessionId="t1" git={GIT} refreshKey={0} />);
    const line = await screen.findByTestId("branch-ci-watch", undefined, LONG);
    expect(line.textContent).toContain("Iron Jarvis is watching its checks.");
    expect(line.textContent).toContain("2 fix rounds left");
    expect(line.textContent).toContain("Next look at");
    expect(line.getAttribute("data-state")).toBe("watching");
  });

  it("gave up: the daemon's own sentence, in the warning tone", async () => {
    primeBranch({
      watch: {
        state: "gave_up",
        rounds: 3,
        max_rounds: 3,
        failed: ["tests"],
        words: "Iron Jarvis stopped after 3 rounds: Claude Code did not approve the fix.",
        next_check_at: null,
      },
      on: true,
    });
    render(<BranchView sessionId="t1" git={GIT} refreshKey={0} />);
    const line = await screen.findByTestId("branch-ci-watch", undefined, LONG);
    expect(line.textContent).toBe("Iron Jarvis stopped after 3 rounds: Claude Code did not approve the fix.");
    expect(line.className).toContain("text-tone-warn");
  });

  it("shows nothing with no watch, and nothing from an older daemon's 404", async () => {
    primeBranch({ watch: null, on: true });
    const first = render(<BranchView sessionId="t1" git={GIT} refreshKey={0} />);
    await screen.findByTestId("branch-pr-checks", undefined, LONG);
    await waitFor(() => expect(sent("GET", "/build/sessions/t1/ci").length).toBeGreaterThan(0), LONG);
    expect(screen.queryByTestId("branch-ci-watch")).toBeNull();
    first.unmount();
    api.calls = [];
    primeBranch(undefined); // 404
    render(<BranchView sessionId="t1" git={GIT} refreshKey={0} />);
    await screen.findByTestId("branch-pr-checks", undefined, LONG);
    await waitFor(() => expect(sent("GET", "/build/sessions/t1/ci").length).toBeGreaterThan(0), LONG);
    expect(screen.queryByTestId("branch-ci-watch")).toBeNull();
  });

  it("is never read for a session with no pull request", async () => {
    primeBranch({ watch: { state: "watching", rounds: 0, max_rounds: 3 }, on: true });
    render(<BranchView sessionId="t1" git={{ ...GIT, prUrl: "" }} refreshKey={0} />);
    await waitFor(() => expect(sent("GET", "/build/sessions/t1/git").length).toBeGreaterThan(0), LONG);
    expect(sent("GET", "/build/sessions/t1/ci")).toHaveLength(0);
  });

  it("words: off, closed, fixing, a past next look", () => {
    const view = (watch: Record<string, unknown>, on = true) => decodeCiView({ watch, on });
    expect(ciWatchWords(view({ state: "watching", rounds: 0 }, false))).toContain("turned off in Settings");
    expect(ciWatchWords(view({ state: "closed" }))).toBe("");
    expect(ciWatchWords(view({ state: "fixing", rounds: 2, max_rounds: 3 }))).toBe(
      "Iron Jarvis is fixing a failed check (round 2 of 3).",
    );
    const past = view({ state: "watching", rounds: 0, next_check_at: "2026-01-01T00:00:00" });
    expect(ciWatchWords(past, new Date("2026-06-01T00:00:00Z"))).toBe("Iron Jarvis is watching its checks.");
    expect(utcMoment("2026-11-06T10:00:00")?.toISOString()).toBe("2026-11-06T10:00:00.000Z");
    expect(decodeCiView({ watch: null, on: true }).watch).toBeNull();
  });
});

/* ------------------------------------------ 4. remove the calendar link */

describe("4. a saved calendar link can be removed from the Reflex page", () => {
  it("two presses send DELETE /triggers/calendar/url and say it was removed", async () => {
    // The daemon's real shape (routes/triggers.py `_status`, pinned in
    // tests/test_cx05_api.py): everything under `triggers`.
    api.responses["/triggers"] = {
      triggers: { calendar: { enabled: true, has_url: true, lead_minutes: 15, tick_seconds: 300 } },
    };
    api.sends["DELETE /triggers/calendar/url"] = { removed: true };
    render(<ReflexPage />);
    const slot = await screen.findByTestId("calendar-url-remove", undefined, LONG);
    fireEvent.click(within(slot).getByRole("button", { name: "Remove" }));
    expect(sent("DELETE", "/triggers/calendar/url")).toHaveLength(0);
    fireEvent.click(within(slot).getByRole("button", { name: "Remove the link?" }));
    await waitFor(() => expect(sent("DELETE", "/triggers/calendar/url")).toHaveLength(1), LONG);
    expect(
      await screen.findByText(/The saved calendar link was removed\./, undefined, LONG),
    ).toBeInTheDocument();
    // The switch is left alone: nothing was posted to the trigger itself.
    expect(sent("POST", "/triggers/calendar")).toHaveLength(0);
  });

  it("offers nothing when no link is saved", async () => {
    api.responses["/triggers"] = { triggers: { calendar: { enabled: true, has_url: false } } };
    render(<ReflexPage />);
    // Wait for the daemon's answer to be drawn (the switch reads on).
    await screen.findByText("on", undefined, LONG);
    expect(screen.queryByTestId("calendar-url-remove")).toBeNull();
  });

  it("reads the daemon's nested answer: a saved link and working email/Slack are not shown as off", async () => {
    api.responses["/triggers"] = {
      triggers: {
        email: { enabled: true, credentialed: true, channels: ["work-mail"] },
        calendar: { enabled: true, has_url: true, lead_minutes: 15 },
        slack: { enabled: true, credentialed: false, channels: ["slack"] },
      },
    };
    render(<ReflexPage />);
    expect(await screen.findByText("URL saved", undefined, LONG)).toBeInTheDocument();
    // Email is on and can receive: active. Slack has no bot token: not set up.
    expect(screen.getAllByText("active")).toHaveLength(1);
    expect(screen.getAllByText("not set up")).toHaveLength(1);
  });
});

/* -------------------------------------------------- 5 + 6. Help's words */

function helpSource(): string {
  return readFileSync(join(__dirname, "..", "app", "help", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
}

describe("5. Help never names the tray's Restart Iron Jarvis as always there", () => {
  it("says the item appears only once the automatic restarts gave up", () => {
    const src = helpSource();
    expect(src).not.toContain("(or tray \u2192 <Kbd>Restart Iron Jarvis</Kbd>)");
    const at = src.indexOf("<Kbd>Restart Iron Jarvis</Kbd>");
    expect(at).toBeGreaterThan(-1);
    const near = src.slice(Math.max(0, at - 200), at + 200).replace(/\s+/g, " ");
    expect(near).toContain("item only once its own automatic restarts have given up");
  });

  it("the tray really adds that item only when a service is capped or stalled", () => {
    const main = readFileSync(join(__dirname, "..", "..", "desktop", "main.js"), "utf8").replace(/\r\n/g, "\n");
    const at = main.indexOf('[{ label: "Restart Iron Jarvis", click: () => restartServicesFromTray() }]');
    expect(at).toBeGreaterThan(-1);
    expect(main.slice(at - 200, at)).toContain("r.capped || r.stalled");
  });
});

describe("6. Help shows the hotkeys the desktop app really registered", () => {
  function shell(state: unknown) {
    (window as unknown as { ironjarvis: unknown }).ironjarvis = {
      isDesktop: true,
      shell: { getState: () => Promise.resolve(state) },
    };
  }

  it("reads the registered keys, and names the preferred key another app took", async () => {
    shell({
      hotkeys: { window: "Ctrl+Alt+J", spotlight: "Ctrl+Shift+Space" },
      preferred: { window: "Ctrl+Shift+J", spotlight: "Ctrl+Shift+Space" },
    });
    render(<GlobalHotkeys />);
    const win = await screen.findByTestId("help-hotkey-window", undefined, LONG);
    expect(win.textContent).toContain("Ctrl+Alt+J");
    expect(win.textContent).toContain("Ctrl+Shift+J is taken by another app");
    const spot = screen.getByTestId("help-hotkey-spotlight");
    expect(spot.textContent).toContain("Ctrl+Shift+Space");
    expect(spot.textContent).not.toContain("taken");
  });

  it("a key nobody could register is never printed as a key", async () => {
    shell({
      hotkeys: { window: null, spotlight: "Ctrl+Shift+Space" },
      preferred: { window: "Ctrl+Shift+J", spotlight: "Ctrl+Shift+Space" },
    });
    render(<GlobalHotkeys />);
    const win = await screen.findByTestId("help-hotkey-window", undefined, LONG);
    expect(win.querySelector("kbd")).toBeNull();
    expect(win.textContent).toContain("No key right now");
    expect(win.textContent).toContain("Open Iron Jarvis");
  });

  it("in a browser tab, says the keys are the desktop app's", async () => {
    render(<GlobalHotkeys />);
    expect((await screen.findByTestId("help-hotkeys-none", undefined, LONG)).textContent).toBe(
      BROWSER_HOTKEYS_WORDS,
    );
  });

  it("a refused read or an older shell says it was not told, never a guessed key", async () => {
    shell(null);
    render(<GlobalHotkeys />);
    expect((await screen.findByTestId("help-hotkeys-none", undefined, LONG)).textContent).toBe(
      UNREAD_HOTKEYS_WORDS,
    );
    expect(hotkeysOf({ platform: "win32" })).toBeNull();
  });

  it("the Help page mounts the card and hard-codes no hotkey", () => {
    const src = helpSource();
    expect(src).toContain("<GlobalHotkeys />");
    expect(src).not.toMatch(/Ctrl\+(Shift|Alt)\+(J|Space)/);
    expect(hotkeyLines({ window: "Ctrl+Shift+J", spotlight: null, preferred: { window: "Ctrl+Shift+J", spotlight: "Ctrl+Shift+Space" } })[1].key).toBeNull();
  });
});
