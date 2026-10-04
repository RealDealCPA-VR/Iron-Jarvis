/**
 * v1.303.3 — a finished sign-in is SEEN.
 *
 * The user's live report: after Sign in on the Iron-Proxy card and a good
 * login in the Build pane, the card kept saying "Needs sign-in", and Claude
 * Code's one-time first-run welcome looked like a second login. These tests
 * guard the dashboard half:
 *
 *  - a needs-sign-in row has **Check again** (POST …/check): a still-signed-out
 *    answer says so in words, a signed-in one says "Signed in — <title> is
 *    ready."; the list is re-read either way;
 *  - after Sign in on a signed-out account, the card re-reads the LIGHT view
 *    every second (3 minutes at most) — across leaving and coming back, since
 *    Sign in opens Build — and says "Signed in — <title> is ready." once;
 *  - a running Iron-Proxy that is too old is shown prominently, with what it
 *    breaks;
 *  - in Build, a sign-in pane whose activity row says `signed_in` shows what to
 *    do next and what the welcome is (through the real page).
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------- */

const hooks = vi.hoisted(() => {
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
    responses: {} as Record<string, unknown>,
    gets: [] as string[],
    calls: [] as Array<{ method: string; path: string; body: unknown }>,
    results: {} as Record<string, unknown>,
    fail: {} as Record<string, { status: number; message: string }>,
    push: vi.fn(),
    /** Paths whose GET is HELD (a slow daemon) until the test releases it. */
    hold: {} as Record<string, boolean>,
    pending: [] as Array<() => void>,
  };
});

vi.mock("@/lib/api", () => {
  const send = (method: string, path: string, body?: unknown) => {
    hooks.calls.push({ method, path, body });
    const key = `${method} ${path}`;
    const f = hooks.fail[key];
    if (f) return Promise.reject(new hooks.FakeApiError(f.message, f.status));
    return Promise.resolve(key in hooks.results ? hooks.results[key] : {});
  };
  return {
    ApiError: hooks.FakeApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: (path: string) => {
      hooks.gets.push(path);
      if (hooks.hold[path]) {
        return new Promise((resolve) => hooks.pending.push(() => resolve(hooks.responses[path])));
      }
      const r = hooks.responses[path];
      if (r === undefined) {
        return Promise.reject(new hooks.FakeApiError(`unmocked GET ${path}`, 404));
      }
      return Promise.resolve(r);
    },
    post: (path: string, body?: unknown) => send("POST", path, body),
    patch: (path: string, body?: unknown) => send("PATCH", path, body),
    put: (path: string, body?: unknown) => send("PUT", path, body),
    del: (path: string) => send("DELETE", path),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: hooks.push, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/connections",
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "layout"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

/* ---- the Build page: the pane stub renders the REAL strip ------------------- */

vi.mock("next/dynamic", async () => {
  const { SignedInStrip } = await import("@/components/terminal/ContinueStrip");
  function TerminalPaneStub(props: { info: { id: string }; paneSignedIn?: { title: string } | null }) {
    return (
      <div data-testid={`terminal-pane-${props.info.id}`}>
        <SignedInStrip paneId={props.info.id} signedIn={props.paneSignedIn} />
      </div>
    );
  }
  return {
    default: () => {
      function DynamicStub(props: Record<string, unknown>) {
        if (typeof props.paneId === "string") return <div data-testid={`pane-chat-${props.paneId}`} />;
        return <TerminalPaneStub {...(props as unknown as Parameters<typeof TerminalPaneStub>[0])} />;
      }
      return DynamicStub;
    },
  };
});
vi.mock("react-rnd", () => ({
  Rnd: ({ children }: { children?: React.ReactNode }) => <div data-testid="rnd">{children}</div>,
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div /> }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div /> }));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

import TerminalsPage from "@/app/terminals/page";
import {
  IronProxyCard,
  checkedStatus,
  isOutdatedError,
  resetSignInWatch,
} from "@/components/connections/IronProxyCard";
import { SignedInStrip, resetContinueMemory } from "@/components/terminal/ContinueStrip";
import { type IronProxyAccount } from "@/lib/ironProxy";
import { signedInOf } from "@/lib/paneAccounts";

/* ---- fixtures -------------------------------------------------------------- */

const LIGHT = "/iron-proxy?discover=0";
const OUTDATED =
  "The Iron-Proxy running on this PC is older than this Iron Jarvis needs — update it, or close it so Iron Jarvis starts its own.";

function acct(over: Partial<IronProxyAccount> & Record<string, unknown>): IronProxyAccount {
  return {
    id: "a",
    title: "Work Max",
    provider: "anthropic",
    lane: "cli",
    order: 0,
    enabled: true,
    state: { status: "ready", served: 0 },
    ...over,
  } as IronProxyAccount;
}

const SIGNED_OUT = acct({ id: "d", title: "Personal", order: 1, state: { status: "unauthenticated" } });
const SIGNED_IN = acct({ id: "d", title: "Personal", order: 1, state: { status: "ready" } });
const WORK = acct({ id: "a", title: "Work Max", order: 0 });

function snap(accounts: IronProxyAccount[], status: Record<string, unknown> = {}) {
  return {
    status: { enabled: true, running: true, owned: true, error: null, ...status },
    accounts,
    discovered: [],
    providers_used_by_jarvis: { anthropic: "claude-cli" },
  };
}

/** Both reads answer the same snapshot (the card merges them). */
function serve(s: unknown) {
  hooks.responses[LIGHT] = s;
  hooks.responses["/iron-proxy"] = s;
}

const n = (p: string) => hooks.gets.filter((g) => g === p).length;
const callsTo = (method: string, path: string) =>
  hooks.calls.filter((c) => c.method === method && c.path === path);
const src = (...parts: string[]) =>
  readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");

async function mountCard() {
  render(<IronProxyCard />);
  await waitFor(() => expect(document.getElementById("iron-proxy-card")).not.toBeNull());
}

/** Count light reads over a real window of time — in short act() slices, so
 *  React flushes each poll tick's fetch as it happens (one long act() batches
 *  every tick into a single fetch at the end). */
async function lightReadsOver(ms: number): Promise<number> {
  const before = n(LIGHT);
  for (let waited = 0; waited < ms; waited += 100) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
  }
  return n(LIGHT) - before;
}

beforeEach(() => {
  localStorage.clear();
  resetSignInWatch();
  resetContinueMemory();
  hooks.responses = {};
  hooks.gets = [];
  hooks.calls = [];
  hooks.results = {};
  hooks.fail = {};
  hooks.push.mockReset();
  hooks.hold = {};
  hooks.pending = [];
});

afterEach(cleanup);

/* ---- 1. seams -------------------------------------------------------------- */

describe("helpers", () => {
  it("reads the account's status out of the check answer, in any of its shapes", () => {
    expect(checkedStatus({ state: { status: "ready" } })).toBe("ready");
    expect(checkedStatus({ account: { state: { status: "unauthenticated" } } })).toBe("unauthenticated");
    expect(checkedStatus({ profile: { status: "active" } })).toBe("active");
    expect(checkedStatus({ status: "ready" })).toBe("ready");
    expect(checkedStatus({})).toBeNull();
    expect(checkedStatus(null)).toBeNull();
  });

  it("knows the daemon's too-old sentence", () => {
    expect(isOutdatedError(OUTDATED)).toBe(true);
    expect(isOutdatedError("Iron-Proxy refused Iron Jarvis's access token; turn Iron-Proxy off and on again.")).toBe(
      false,
    );
    expect(isOutdatedError(null)).toBe(false);
  });

  it("a sign-in pane's activity row: signed_in + the account's title", () => {
    expect(signedInOf({ signed_in: true, name: "Sign in: Personal" })).toEqual({ title: "Personal" });
    expect(signedInOf({ signed_in: { title: "Home Max" }, name: "Sign in: x" })).toEqual({ title: "Home Max" });
    expect(signedInOf({ signed_in: true, sign_in_account: { id: "d", title: "Personal" } })).toEqual({
      title: "Personal",
    });
    expect(signedInOf({ signed_in: true })).toEqual({ title: "this account" });
    expect(signedInOf({ signed_in: false, name: "Sign in: Personal" })).toBeNull();
    expect(signedInOf({ name: "Sign in: Personal" })).toBeNull();
  });
});

/* ---- 2. Check again -------------------------------------------------------- */

describe("Check again", () => {
  it("only a signed-out CLI row has it", async () => {
    serve(snap([WORK, SIGNED_OUT]));
    await mountCard();
    await screen.findByTestId("iron-proxy-check-d");
    expect(screen.queryByTestId("iron-proxy-check-a")).toBeNull();
  });

  it("still signed out: says so in words, and re-reads the list", async () => {
    serve(snap([WORK, SIGNED_OUT]));
    hooks.results["POST /iron-proxy/accounts/d/check"] = { id: "d", state: { status: "unauthenticated" } };
    await mountCard();
    const before = n(LIGHT);
    fireEvent.click(await screen.findByTestId("iron-proxy-check-d"));
    expect(await screen.findByText("Still not signed in — finish the login in the Sign in pane.")).toBeTruthy();
    expect(callsTo("POST", "/iron-proxy/accounts/d/check")).toHaveLength(1);
    await waitFor(() => expect(n(LIGHT)).toBeGreaterThan(before));
  });

  it("signed in now: says it is ready and the chip shows the new state", async () => {
    serve(snap([WORK, SIGNED_OUT]));
    hooks.results["POST /iron-proxy/accounts/d/check"] = { account: { id: "d", state: { status: "ready" } } };
    await mountCard();
    serve(snap([WORK, SIGNED_IN])); // what the re-read will answer
    fireEvent.click(await screen.findByTestId("iron-proxy-check-d"));
    expect(await screen.findByText("Signed in — Personal is ready.")).toBeTruthy();
    await waitFor(() =>
      expect(document.getElementById("iron-proxy-account-d")!.getAttribute("data-status")).toBe("ready"),
    );
  });
});

/* ---- 3. after Sign in ------------------------------------------------------ */

describe("after Sign in, the card watches that account", () => {
  it("re-reads the light view every second, across leaving for Build and coming back; says ready ONCE", async () => {
    serve(snap([WORK, SIGNED_OUT]));
    hooks.results["POST /iron-proxy/accounts/d/signin"] = { terminal_id: "t-sign", name: "Sign in: Personal" };
    await mountCard();
    fireEvent.click(document.getElementById("iron-proxy-account-d")!.querySelector("button")!); // Sign in
    await waitFor(() => expect(hooks.push).toHaveBeenCalledWith("/terminals?focus=t-sign"));
    // Sign in opened Build: the card unmounts, and comes back when the user does.
    cleanup();
    await mountCard();
    expect(await lightReadsOver(3500)).toBeGreaterThanOrEqual(2);
    // The login finished in the Build pane: the chip flips without a reload.
    serve(snap([WORK, SIGNED_IN]));
    const note = await screen.findByTestId("iron-proxy-signed-in", {}, { timeout: 4000 });
    expect(note.textContent).toBe("Signed in — Personal is ready.");
    expect(document.getElementById("iron-proxy-account-d")!.getAttribute("data-status")).toBe("ready");
    // Watching stopped: back to the 5 s cadence.
    expect(await lightReadsOver(2500)).toBeLessThanOrEqual(1);
  });

  it("an account that is already usable is not watched (the 5 s cadence stays)", async () => {
    serve(snap([WORK, SIGNED_OUT]));
    hooks.results["POST /iron-proxy/accounts/a/signin"] = { terminal_id: "t-a" };
    await mountCard();
    fireEvent.click(document.getElementById("iron-proxy-account-a")!.querySelector("button")!);
    await waitFor(() => expect(hooks.push).toHaveBeenCalledWith("/terminals?focus=t-a"));
    expect(await lightReadsOver(2500)).toBeLessThanOrEqual(1);
    // …and nothing claims a sign-in happened that the user never needed.
    expect(screen.queryByTestId("iron-proxy-signed-in")).toBeNull();
  });

  it("never STACKS reads: a slow answer holds the next tick (no second GET until the first resolves)", async () => {
    serve(snap([WORK, SIGNED_OUT]));
    hooks.results["POST /iron-proxy/accounts/d/signin"] = { terminal_id: "t-sign" };
    await mountCard();
    fireEvent.click(document.getElementById("iron-proxy-account-d")!.querySelector("button")!);
    await waitFor(() => expect(hooks.push).toHaveBeenCalledWith("/terminals?focus=t-sign"));
    expect(await lightReadsOver(2500)).toBeGreaterThanOrEqual(2); // the 1 s watch is on
    // The daemon slows down (Iron-Proxy being replaced): one read hangs.
    hooks.hold[LIGHT] = true;
    expect(await lightReadsOver(4000)).toBe(1); // that one — and no other, at 1 s ticks
    // It answers: the watch carries on.
    hooks.hold = {};
    for (const release of hooks.pending.splice(0)) release();
    expect(await lightReadsOver(2500)).toBeGreaterThanOrEqual(1);
  });

  it("every Iron-Proxy read on the card goes through the non-stacking poll (pinned)", () => {
    const card = src("components", "connections", "IronProxyCard.tsx");
    expect(card).not.toMatch(/usePolledApi\s*[<(]/);
    expect(card).toContain("const light = useSerialPolledApi<IronProxySnapshot>(");
    expect(card).toContain('const full = useSerialPolledApi<IronProxySnapshot>("/iron-proxy", IRON_PROXY_DISCOVER_POLL_MS);');
  });

  it("watches for three minutes at most (pinned)", () => {
    const card = src("components", "connections", "IronProxyCard.tsx");
    expect(card).toContain("export const SIGNIN_WATCH_MS = 3 * 60_000;");
    expect(card).toContain("export const IRON_PROXY_SIGNIN_POLL_MS = 1000;");
    expect(card).toMatch(
      /const t = setTimeout\(\(\) => \{\n\s+signInWatch = null;\n\s+setWatch\(null\);\n\s+\}, Math\.max\(0, watch\.until - Date\.now\(\)\)\);/,
    );
  });
});

/* ---- 4. a too-old Iron-Proxy ---------------------------------------------- */

describe("a running Iron-Proxy that is too old", () => {
  it("is shown prominently, with what it breaks", async () => {
    serve(snap([WORK], { error: OUTDATED }));
    await mountCard();
    const banner = await waitFor(() => {
      const el = document.getElementById("iron-proxy-outdated");
      expect(el).not.toBeNull();
      return el!;
    });
    expect(banner.getAttribute("role")).toBe("alert");
    expect(banner.textContent).toContain(OUTDATED);
    expect(banner.textContent).toContain("Build's “as <account>” launches and account switching are refused");
  });

  it("another running error (token refused) is shown plainly, not as the too-old banner", async () => {
    const refused = "Iron-Proxy refused Iron Jarvis's access token; turn Iron-Proxy off and on again.";
    serve(snap([WORK], { error: refused }));
    await mountCard();
    expect(await screen.findByText(refused)).toBeTruthy();
    expect(document.getElementById("iron-proxy-outdated")).toBeNull();
  });
});

/* ---- 5. Build: the sign-in pane's login finished --------------------------- */

describe("Build: Signed in", () => {
  it("says where, what to type, and what the welcome is; Dismiss hides it", () => {
    render(<SignedInStrip paneId="t-sign" signedIn={{ title: "Personal" }} />);
    const el = document.getElementById("pane-signed-in-t-sign")!;
    expect(el.textContent).toBe(
      "Signed in to “Personal”. Type claude here to use it — Claude Code shows its one-time welcome the first time.",
    );
    expect(el.querySelector("code")!.textContent).toBe("claude");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(document.getElementById("pane-signed-in-t-sign")).toBeNull();
  });

  it("nothing for a pane that is not a finished sign-in", () => {
    render(<SignedInStrip paneId="t1" signedIn={null} />);
    expect(document.getElementById("pane-signed-in-t1")).toBeNull();
  });

  it("through the page: only the sign-in pane whose row says signed_in shows it", async () => {
    const term = (id: string) => ({
      id,
      cwd: "C:\\Users\\VR",
      shell: "pwsh",
      argv: [],
      cols: 120,
      rows: 30,
      alive: true,
      exit_code: null,
      created_at: "2026-10-04T00:00:00Z",
    });
    hooks.responses = {
      "/terminals": { terminals: [term("t1"), term("t-sign")] },
      "/terminals/shells": { shells: [] },
      "/models": { models: [] },
      "/terminals/ai-clis": { clis: [] },
      "/skills": { skills: [] },
      "/terminals/activity": {
        panes: [{ id: "t1" }, { id: "t-sign", name: "Sign in: Personal", signed_in: true }],
      },
    };
    render(<TerminalsPage />);
    await waitFor(() => expect(document.getElementById("pane-signed-in-t-sign")).not.toBeNull());
    expect(document.getElementById("pane-signed-in-t-sign")!.textContent).toContain("Signed in to “Personal”.");
    expect(document.getElementById("pane-signed-in-t1")).toBeNull();
  });

  it("TerminalPane mounts it and the page passes the row (source pins)", () => {
    expect(src("components", "terminal", "TerminalPane.tsx")).toContain(
      "<SignedInStrip paneId={info.id} signedIn={paneSignedIn} />",
    );
    expect(src("app", "terminals", "page.tsx")).toContain("paneSignedIn={signedInOf(act)}");
  });
});
