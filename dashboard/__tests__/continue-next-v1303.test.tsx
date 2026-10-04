/**
 * v1.303.0 — Continue on the next account.
 *
 * A Claude pane runs on one account for its life; when that account runs out
 * the daemon's activity row carries `limit`, and the pane offers to open a NEW
 * pane on the next free Claude account, carrying the conversation over when it
 * can. These tests guard:
 *
 *  - when the strip shows (an Iron-Proxy Claude account WITH a limit — never
 *    this PC's login, never without a limit) and that its Iron-Proxy read is
 *    the LIGHT view, made only while it is shown;
 *  - what it says: the account, the reset (Iron-Proxy's local time first), the
 *    account it would move to; "frees at <local time>" and no button when every
 *    other account is parked; no button when none can take over;
 *  - Dismiss hides it until the limit LINE changes;
 *  - the press: POST /terminals/{id}/continue-on-next, the new pane handed to
 *    the page and FOCUSED (through the real page), the new pane's
 *    "Continued from …" line with the answer's note until dismissed; a 409
 *    sentence shown with no pane.
 *
 * TerminalPane cannot mount in jsdom (xterm): its call sites are SOURCE-PINNED,
 * read with CRLF normalised.
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
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/terminals",
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

/* ---- the Build page: the pane stub renders the REAL strip with the props the
 * page hands TerminalPane, so the press is driven through the page. ---------- */

vi.mock("next/dynamic", async () => {
  const { PaneContinue, ContinuedFromLine } = await import("@/components/terminal/ContinueStrip");
  function TerminalPaneStub(props: {
    info: { id: string; shell: string };
    onOpenedPane?: (p: { id: string; [k: string]: unknown }) => void;
    liveAccounts?: import("@/lib/paneAccounts").PaneAccounts | null;
    paneLimit?: import("@/lib/paneAccounts").PaneLimit | null;
  }) {
    return (
      <div data-testid={`terminal-pane-${props.info.id}`}>
        <PaneContinue
          paneId={props.info.id}
          accounts={props.liveAccounts}
          limit={props.paneLimit}
          onOpened={(p) => props.onOpenedPane?.(p)}
        />
        <ContinuedFromLine paneId={props.info.id} />
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
  ContinuedFromLine,
  PaneContinue,
  continuedInfo,
  resetContinueMemory,
} from "@/components/terminal/ContinueStrip";
import { localTime, type IronProxyAccount } from "@/lib/ironProxy";
import {
  continuableAccount,
  continueNote,
  LIMIT_WORDS,
  limitKey,
  limitResetWords,
  limitWords,
  nextAccount,
  paneAccountsOf,
  paneLimitOf,
  type ContinueAnswer,
  type PaneAccounts,
  type PaneLimit,
} from "@/lib/paneAccounts";

/* ---- fixtures -------------------------------------------------------------- */

const LIGHT = "/iron-proxy?discover=0";

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

function snap(accounts: IronProxyAccount[], status: Record<string, unknown> = {}) {
  return {
    status: { enabled: true, running: true, owned: true, error: null, ...status },
    accounts,
    discovered: [],
  };
}

function isoTodayAt(h: number, m: number): string {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.toISOString();
}
const AT_1505 = isoTodayAt(15, 5);
const AT_1630 = isoTodayAt(16, 30);

const parked = (until: string) => ({ status: "parked", parkedUntil: until, parkedReason: { kind: "quota-exhausted" } });

const WORK = acct({ id: "a", title: "Work Max", order: 0, state: parked(AT_1505) });
const PERSONAL = acct({ id: "b", title: "Personal", order: 1 });
const SIGNED_OUT = acct({ id: "d", title: "Old Login", order: 2, state: { status: "unauthenticated" } });
const OFF = acct({ id: "e", title: "Switched Off", order: 3, enabled: false });
const GROK = acct({ id: "x", title: "Grok main", provider: "xai", order: 0 });

const LIMIT: PaneLimit = { line: "Usage limit reached · resets 3pm", reset_words: "resets 3pm", since: "2026-10-03T14:00:00Z" };

const ON_WORK: PaneAccounts = paneAccountsOf({
  accounts: { anthropic: { id: "a", title: "Work Max", source: "iron-proxy", state: "parked", until: AT_1505 } },
})!;

const ANSWER: ContinueAnswer = {
  pane: { id: "t-next", cwd: "C:\\proj\\t1", shell: "pwsh", alive: true },
  resumed: true,
  session_id: "s-1",
  parked: true,
  from: { id: "a", title: "Work Max" },
  to: { id: "b", title: "Personal" },
  note: "Carried your conversation over to “Personal”.",
};

const n = (p: string) => hooks.gets.filter((g) => g === p).length;
const callsTo = (method: string, path: string) =>
  hooks.calls.filter((c) => c.method === method && c.path === path);
const src = (...parts: string[]) =>
  readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");
const strip = (id = "t1") => document.getElementById(`pane-continue-${id}`);
const go = (id = "t1") => screen.queryByTestId(`pane-continue-go-${id}`) as HTMLButtonElement | null;

beforeEach(() => {
  localStorage.clear();
  resetContinueMemory();
  hooks.responses = {};
  hooks.gets = [];
  hooks.calls = [];
  hooks.results = {};
  hooks.fail = {};
});

afterEach(cleanup);

/* ---- 1. the seams ---------------------------------------------------------- */

describe("helpers", () => {
  it("reads the activity row's limit; anything else is no limit", () => {
    expect(paneLimitOf({ limit: LIMIT })).toMatchObject(LIMIT);
    expect(
      paneLimitOf({ limit: { line: "You've hit your weekly limit", kind: "weekly", reset_at: AT_1630 } }),
    ).toMatchObject({ kind: "weekly", reset_at: AT_1630 });
    expect(paneLimitOf({ limit: { line: "" } })).toBeNull();
    expect(paneLimitOf({ id: "t1" })).toBeNull();
    expect(paneLimitOf(undefined)).toBeNull();
  });

  it("only an Iron-Proxy Claude account can be continued", () => {
    expect(continuableAccount(ON_WORK)!.id).toBe("a");
    expect(
      continuableAccount(paneAccountsOf({ accounts: { anthropic: { id: null, title: "this PC's login", source: "default" } } })),
    ).toBeNull();
    expect(continuableAccount(paneAccountsOf({ accounts: { xai: { id: "x", title: "Grok", source: "iron-proxy" } } }))).toBeNull();
    expect(continuableAccount(null)).toBeNull();
  });

  it("the reset: Iron-Proxy's parked-until in LOCAL time first, else the CLI's words", () => {
    expect(limitResetWords(ON_WORK.anthropic, LIMIT)).toBe(`resets ${localTime(AT_1505)}`);
    const ready = { ...ON_WORK.anthropic, state: "active", until: null };
    expect(limitResetWords(ready, LIMIT)).toBe("resets 3pm");
    expect(limitResetWords(ready, { line: "x", reset_words: "in 2 hours" })).toBe("resets in 2 hours");
    expect(limitResetWords(ready, { line: "x" })).toBeNull();
    // The CLI's own exact reset (the daemon's `reset_at`) wins, in LOCAL time.
    expect(limitResetWords(ON_WORK.anthropic, { ...LIMIT, reset_at: AT_1630 })).toBe(
      `resets ${localTime(AT_1630)}`,
    );
    expect(limitResetWords(ready, { line: "x", reset_at: "not a time", reset_words: "resets 3:45pm (America/New_York)" })).toBe(
      "resets 3:45pm (America/New_York)",
    );
  });

  it("names the kind of limit when the daemon does; plain 'usage limit' otherwise", () => {
    // Every kind the daemon reports, and nothing else.
    expect(LIMIT_WORDS).toEqual({
      session: "hit its session limit",
      weekly: "hit its weekly limit",
      daily: "hit its daily limit",
      monthly: "hit its monthly limit",
      usage: "hit its usage limit",
      credits: "ran out of usage credits",
      model: "hit its limit for that model",
    });
    for (const [kind, words] of Object.entries(LIMIT_WORDS)) {
      expect(limitWords({ line: "x", kind })).toBe(words);
    }
    expect(limitWords({ line: "x", kind: "WEEKLY" })).toBe("hit its weekly limit");
    expect(limitWords({ line: "x", kind: "something-new" })).toBe("hit its usage limit");
    expect(limitWords({ line: "x" })).toBe("hit its usage limit");
  });

  it("Dismiss keys on the limit EPISODE (since), never the repainting line", () => {
    expect(limitKey({ line: "resets in 4m", since: "S1" })).toBe(limitKey({ line: "resets in 3m", since: "S1" }));
    expect(limitKey({ line: "a", since: "S1" })).not.toBe(limitKey({ line: "a", since: "S2" }));
  });

  it("the next account: the first usable other Claude CLI account, in order", () => {
    expect(nextAccount(null, "a")).toEqual({ kind: "unknown" });
    expect(nextAccount(snap([WORK, PERSONAL], { running: false }), "a")).toEqual({ kind: "unknown" });
    const s = snap([SIGNED_OUT, OFF, GROK, PERSONAL, WORK]);
    expect(nextAccount(s, "a")).toMatchObject({ kind: "next", account: { id: "b" } });
    // Every other one parked: the one that frees FIRST.
    const later = acct({ id: "c", title: "Later", order: 1, state: parked(AT_1630) });
    const sooner = acct({ id: "f", title: "Sooner", order: 2, state: parked(AT_1505) });
    expect(nextAccount(snap([WORK, later, sooner, SIGNED_OUT]), "a")).toMatchObject({
      kind: "wait",
      account: { id: "f" },
      until: AT_1505,
    });
    expect(nextAccount(snap([WORK, SIGNED_OUT, OFF, GROK]), "a")).toEqual({ kind: "none" });
  });

  it("the note: the daemon's words first; else built from resumed / parked", () => {
    expect(continueNote(ANSWER)).toBe("Carried your conversation over to “Personal”.");
    expect(continueNote({ ...ANSWER, note: null })).toBe("Carried your conversation over.");
    expect(continueNote({ ...ANSWER, note: "", resumed: false })).toBe(
      "Started fresh — there was no conversation to carry over.",
    );
    const several =
      "Several conversations hit the limit at once, so none was carried — use /resume in the new pane.";
    expect(continueNote({ ...ANSWER, resumed: false, note: several })).toBe(several);
    expect(continueNote({ ...ANSWER, note: null, parked: false })).toMatch(
      /Iron-Proxy did not mark “Work Max” as out of usage/,
    );
  });
});

/* ---- 2. the strip ---------------------------------------------------------- */

function mountStrip(
  over: { accounts?: PaneAccounts | null; limit?: PaneLimit | null } = {},
  onOpened = vi.fn(),
) {
  const props = { accounts: ON_WORK, limit: LIMIT, ...over };
  const utils = render(<PaneContinue paneId="t1" accounts={props.accounts} limit={props.limit} onOpened={onOpened} />);
  return { onOpened, rerender: (o: typeof over) => utils.rerender(
    <PaneContinue paneId="t1" accounts={{ ...props, ...o }.accounts} limit={{ ...props, ...o }.limit} onOpened={onOpened} />,
  ) };
}

describe("the Continue strip", () => {
  it("does not show — or read Iron-Proxy — without a limit, or on this PC's login", async () => {
    hooks.responses[LIGHT] = snap([WORK, PERSONAL]);
    mountStrip({ limit: null });
    cleanup();
    mountStrip({
      accounts: paneAccountsOf({ accounts: { anthropic: { id: null, title: "this PC's login", source: "default" } } }),
    });
    cleanup();
    mountStrip({ accounts: null });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(strip()).toBeNull();
    expect(hooks.gets).toEqual([]);
  });

  it("names the account, its LOCAL reset, and the account it moves to — from the LIGHT read only", async () => {
    hooks.responses[LIGHT] = snap([WORK, PERSONAL]);
    hooks.responses["/iron-proxy"] = snap([WORK, PERSONAL]);
    mountStrip();
    await waitFor(() => expect(go()?.textContent).toBe("Continue on “Personal”"));
    expect(strip()!.textContent).toContain(
      `Claude's account “Work Max” hit its usage limit (resets ${localTime(AT_1505)}).`,
    );
    expect(n(LIGHT)).toBeGreaterThan(0);
    expect(n("/iron-proxy")).toBe(0);
  });

  it("says BEFORE the press that the conversation is copied to the other account (F4)", async () => {
    hooks.responses[LIGHT] = snap([WORK, PERSONAL]);
    mountStrip();
    const line = await screen.findByTestId("pane-continue-privacy-t1");
    expect(line.textContent).toBe(
      "Your conversation is copied to “Personal” and continues there (sent to Anthropic under that account); “Work Max” keeps its copy.",
    );
    expect(line.getAttribute("title")).toMatch(/sends the conversation to Anthropic under the account/);
    expect(hooks.calls).toEqual([]); // nothing was pressed
  });

  it("the daemon picks: the copy sentence names 'the next account'", async () => {
    mountStrip(); // LIGHT unmocked -> 404
    await waitFor(() => expect(n(LIGHT)).toBeGreaterThan(0));
    expect(screen.getByTestId("pane-continue-privacy-t1").textContent).toBe(
      "Your conversation is copied to the next account and continues there (sent to Anthropic under that account); “Work Max” keeps its copy.",
    );
  });

  it("no button, no copy sentence when nothing can take over", async () => {
    hooks.responses[LIGHT] = snap([WORK, SIGNED_OUT]);
    mountStrip();
    await waitFor(() => expect(strip()!.textContent).toContain("No other Claude account can take over"));
    expect(screen.queryByTestId("pane-continue-privacy-t1")).toBeNull();
  });

  it("names the kind of limit and the CLI's own reset time", async () => {
    hooks.responses[LIGHT] = snap([WORK, PERSONAL]);
    mountStrip({
      accounts: paneAccountsOf({ accounts: { anthropic: { id: "a", title: "Work Max", source: "iron-proxy", state: "active" } } }),
      limit: { line: "You've hit your weekly limit · resets Oct 7", kind: "weekly", reset_at: AT_1630, since: "S" },
    });
    await waitFor(() => expect(go()).not.toBeNull());
    expect(strip()!.textContent).toContain(
      `Claude's account “Work Max” hit its weekly limit (resets ${localTime(AT_1630)}).`,
    );
  });

  it("every other account parked: says when the first frees, and offers no button", async () => {
    hooks.responses[LIGHT] = snap([WORK, acct({ id: "c", title: "Later", order: 1, state: parked(AT_1630) })]);
    mountStrip();
    await waitFor(() =>
      expect(strip()!.textContent).toContain(`“Later” frees at ${localTime(AT_1630)}.`),
    );
    expect(go()).toBeNull();
    expect(strip()!.textContent).not.toContain(AT_1630);
  });

  it("no other account can take over: says so, no button", async () => {
    hooks.responses[LIGHT] = snap([WORK, SIGNED_OUT]);
    mountStrip();
    await waitFor(() => expect(strip()!.textContent).toContain("No other Claude account can take over"));
    expect(go()).toBeNull();
  });

  it("Iron-Proxy's snapshot not in hand: the daemon picks — 'Continue on the next account'", async () => {
    mountStrip(); // LIGHT unmocked -> 404
    await waitFor(() => expect(n(LIGHT)).toBeGreaterThan(0));
    expect(go()!.textContent).toBe("Continue on the next account");
  });

  it("Dismiss hides it for this limit — a repainting countdown does NOT bring it back (F5)", async () => {
    hooks.responses[LIGHT] = snap([WORK, PERSONAL]);
    const { rerender } = mountStrip();
    await waitFor(() => expect(strip()).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(strip()).toBeNull();
    // The CLI repaints its line (a countdown); same limit, same `since`.
    rerender({ limit: { ...LIMIT, line: "Usage limit reached · resets in 59 min" } });
    expect(strip()).toBeNull();
    rerender({ limit: { ...LIMIT, line: "Usage limit reached · resets in 58 min" } });
    expect(strip()).toBeNull();
    // A NEW limit episode (the daemon saw it start again) asks again.
    rerender({ limit: { ...LIMIT, since: "2026-10-03T19:00:00Z" } });
    expect(strip()).not.toBeNull();
  });

  it("the press POSTs continue-on-next, hands the new pane over, and the new pane says where it came from", async () => {
    hooks.responses[LIGHT] = snap([WORK, PERSONAL]);
    hooks.results["POST /terminals/t1/continue-on-next"] = ANSWER;
    const { onOpened } = mountStrip();
    render(<ContinuedFromLine paneId="t-next" />);
    await waitFor(() => expect(go()).not.toBeNull());
    fireEvent.click(go()!);
    await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1));
    expect(onOpened.mock.calls[0][0]).toMatchObject({ id: "t-next", cwd: "C:\\proj\\t1" });
    expect(callsTo("POST", "/terminals/t1/continue-on-next")).toHaveLength(1);
    // The old pane has been answered: its strip stops asking.
    expect(strip()).toBeNull();
    const line = document.getElementById("pane-continued-t-next")!;
    expect(line.textContent).toBe("Continued from “Work Max” — Carried your conversation over to “Personal”.");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(document.getElementById("pane-continued-t-next")).toBeNull();
    expect(continuedInfo("t-next")).toBeNull();
  });

  it("a refusal shows the daemon's sentence and opens nothing", async () => {
    hooks.responses[LIGHT] = snap([WORK, PERSONAL]);
    hooks.fail["POST /terminals/t1/continue-on-next"] = {
      status: 409,
      message: "Every other Claude account is out of usage — the first frees at 4:30 PM.",
    };
    const { onOpened } = mountStrip();
    await waitFor(() => expect(go()).not.toBeNull());
    fireEvent.click(go()!);
    const err = await screen.findByTestId("pane-continue-error-t1");
    expect(err.textContent).toBe("Every other Claude account is out of usage — the first frees at 4:30 PM.");
    expect(onOpened).not.toHaveBeenCalled();
    expect(strip()).not.toBeNull();
  });
});

/* ---- 3. through the Build page --------------------------------------------- */

const term = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  cwd: `C:\\proj\\${id}`,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-10-03T00:00:00Z",
  ...extra,
});

describe("the Build page", () => {
  it("a limit on the activity row shows the strip; the press adds the new pane and FOCUSES it", async () => {
    const accounts = { anthropic: { id: "a", title: "Work Max", source: "iron-proxy", state: "parked", until: AT_1505 } };
    hooks.responses = {
      "/terminals": { terminals: [term("t1", { accounts }), term("t2")] },
      "/terminals/shells": { shells: [] },
      "/models": { models: [] },
      "/terminals/ai-clis": { clis: [] },
      "/skills": { skills: [] },
      "/terminals/activity": { panes: [{ id: "t1", agent_cli: "claude", accounts, limit: LIMIT }, { id: "t2" }] },
      [LIGHT]: snap([WORK, PERSONAL]),
    };
    hooks.results["POST /terminals/t1/continue-on-next"] = {
      ...ANSWER,
      pane: term("t-next", { accounts: { anthropic: { id: "b", title: "Personal", source: "iron-proxy" } } }),
    };
    render(<TerminalsPage />);
    await waitFor(() => expect(go()?.textContent).toBe("Continue on “Personal”"));
    expect(strip("t2")).toBeNull();
    fireEvent.click(go()!);
    await waitFor(() => expect(screen.getByTestId("rail-pane-t-next").style.visibility).toBe("visible"));
    expect(screen.getByTestId("rail-pane-t1").style.visibility).toBe("hidden");
    expect(screen.getByTestId("rail-account-t-next").textContent).toBe("Claude · Personal");
    expect(document.getElementById("pane-continued-t-next")!.textContent).toBe(
      "Continued from “Work Max” — Carried your conversation over to “Personal”.",
    );
    expect(strip("t1")).toBeNull();
  });
});

/* ---- 4. TerminalPane's call sites (source pins) ---------------------------- */

describe("TerminalPane and the page wire it", () => {
  it("the pane renders the strip from its live accounts + the limit, and the continued line", () => {
    const pane = src("components", "terminal", "TerminalPane.tsx");
    expect(pane).toContain(
      "<PaneContinue paneId={info.id} accounts={paneAccounts} limit={paneLimit} onOpened={openedPane} />",
    );
    expect(pane).toContain("<ContinuedFromLine paneId={info.id} />");
  });

  it("the page hands each pane its activity row's limit", () => {
    expect(src("app", "terminals", "page.tsx")).toContain("paneLimit={paneLimitOf(act)}");
  });

  it("the strip reads only the LIGHT Iron-Proxy view", () => {
    const strip = src("components", "terminal", "ContinueStrip.tsx");
    expect(strip).toContain("usePolledApi<unknown>(IRON_PROXY_LIGHT, LAUNCH_ACCOUNTS_POLL_MS)");
    // The only Iron-Proxy path named in the file is the v1.303.2 sign-in POST
    // for one account — never a GET of the (full) Iron-Proxy view.
    expect(strip.match(/["'`]\/iron-proxy[^"'`]*["'`]/g)).toEqual([
      "`/iron-proxy/accounts/${encodeURIComponent(need.account.id)}/signin`",
    ]);
    expect(src("lib", "paneAccounts.ts")).toContain('export const IRON_PROXY_LIGHT = "/iron-proxy?discover=0";');
  });
});
