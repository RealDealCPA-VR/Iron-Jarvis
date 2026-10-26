/**
 * v1.393.0 (the long-jobs plan's Phase 4, decision 4) — LIMITS WITHOUT A
 * PRESS, the screen's half:
 *
 *  - the limit card while the reply waits by itself says "Waiting for
 *    <account>'s reset at <time>; it carries on by itself", with Cancel AND
 *    Choose account (the Choose account path is the one that existed);
 *  - a reply carried on with another account of the group says both names:
 *    "<A> reached its limit; carried on with <B> (same group).";
 *  - the session's account chip and the Timeline's header show the account's
 *    current usage window from the cached snapshot ("63% of the 5-hour
 *    window, resets 3:40 PM"), and "usage not known yet" — never 0% — before
 *    the vendor sent a number;
 *  - the bell: a limit the reply waits out by itself is news, not a question
 *    (no badge); a move is ONE row naming both accounts.
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
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    postAnswers: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.postAnswers[`${method} ${path}`];
    const value = typeof hit === "function" ? (hit as (p: string) => unknown)(path) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { HarnessTimeline } from "@/components/harness/HarnessTimeline";
import { toActivity } from "@/components/NotificationBell";
import {
  USAGE_NOT_KNOWN,
  accountMoveLine,
  accountUsageLine,
  accountUsageWords,
  decodeAccount,
  decodeAccountMove,
  decodeAccountWait,
  decodeAccountLimit,
  limitWaitingWords,
  resetWords,
  windowAdjective,
} from "@/lib/buildAccounts";
import { localTime } from "@/lib/ironProxy";
import type { BuildSessionView } from "@/lib/buildSessions";

/* ------------------------------------------------------------- fixtures */

const RESET = "2030-01-01T16:12:00Z";
const WINDOW_RESET = "2030-01-01T15:40:00Z";

const LIMIT_RAW = {
  account: "work",
  account_name: "Work",
  provider: "claude-cli",
  vendor: "claude",
  kind: "usage",
  resets_at: RESET,
  resumable: true,
  turn_id: "x",
};
const LIMIT = decodeAccountLimit(LIMIT_RAW)!;
const AUTO_WAIT = { account: "work", account_name: "Work", resets_at: RESET, auto: true };

const WORK_ROW = {
  id: "work",
  name: "TSI",
  vendor: "claude",
  vendor_label: "Claude Code",
  provider: "claude-cli",
  default: true,
  plan: "max",
  plan_label: "Max",
  status: "usage",
  usage: { percent: 63, window: "5 hours", window_mins: 300, resets_at: WINDOW_RESET },
};
const SPARE_ROW = {
  id: "spare",
  name: "Spare",
  vendor: "claude",
  vendor_label: "Claude Code",
  provider: "claude-cli",
  default: false,
  plan: null,
  plan_label: null,
  status: "ready",
};
const ACCOUNTS = {
  claude: [WORK_ROW, SPARE_ROW],
  codex: [],
  iron_proxy: { enabled: true, running: true, needs_update: false, note: "" },
};

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-10-22T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: { provider: "claude-cli", account: "work", account_name: "TSI" },
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    account: WORK_ROW,
    account_vendor: "claude",
    account_limit: null,
    account_wait: null,
    ...over,
  };
}

function sse(frames: [string, unknown][]): Response {
  const text = frames.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode(text));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": ACCOUNTS };
  api.calls = [];
  api.postAnswers = {};
  localStorage.clear();
  fetchMock = vi.fn(async () => sse([["done", { reply: "ok" }]]));
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

/* ------------------------------------------------------------ the words */

describe("the words", () => {
  it("a wait that carries on by itself names the account and its reset ONCE", () => {
    const at = localTime(RESET);
    expect(limitWaitingWords(LIMIT, decodeAccountWait(AUTO_WAIT))).toBe(
      `Waiting for Work's reset at ${at}; it carries on by itself.`,
    );
    // A group may take over first; the sentence says so.
    expect(limitWaitingWords(LIMIT, decodeAccountWait({ ...AUTO_WAIT, group: "Work" }))).toBe(
      `Waiting for Work's reset at ${at} or for another account in the “Work” group; it carries on by itself.`,
    );
    // No reset known: ready, not a time.
    expect(limitWaitingWords({ ...LIMIT, resetsAt: null }, decodeAccountWait({ ...AUTO_WAIT, resets_at: null }))).toBe(
      "Waiting for Work to be ready; it carries on by itself.",
    );
    expect(limitWaitingWords({ ...LIMIT, kind: "signin", resetsAt: null }, null)).toBe(
      "Waiting for Work to be signed in again; it carries on by itself.",
    );
  });

  it("the wait's own facts are whitelisted", () => {
    expect(decodeAccountWait({ ...AUTO_WAIT, group: "Work", once: true, junk: 1 })).toEqual({
      account: "work",
      accountName: "Work",
      resetsAt: RESET,
      auto: true,
      group: "Work",
    });
    expect(decodeAccountWait({ account: "w", auto: "yes" })?.auto).toBe(false);
  });

  it("a move names both accounts, and junk is not a move", () => {
    const move = decodeAccountMove({ from: "work", fromName: "TSI", to: "spare", toName: "Spare", group: "Work" })!;
    expect(accountMoveLine(move)).toBe("TSI reached its limit; carried on with Spare (same group).");
    expect(decodeAccountMove({ from: "work" })).toBeNull();
    expect(decodeAccountMove("TSI")).toBeNull();
  });

  it("usage is the window nearest its limit in words, and never 0% when unknown", () => {
    const work = decodeAccount(WORK_ROW)!;
    expect(accountUsageWords(work)).toBe(`63% of the 5-hour window, ${resetWords(WINDOW_RESET)}`);
    expect(accountUsageLine(work)).toBe(`TSI · 63% of the 5-hour window, ${resetWords(WINDOW_RESET)}`);
    const spare = decodeAccount(SPARE_ROW)!;
    expect(accountUsageWords(spare)).toBe(USAGE_NOT_KNOWN);
    expect(accountUsageWords(spare)).not.toMatch(/0%/);
    expect(windowAdjective("7 days")).toBe("7-day");
    expect(windowAdjective("1 hour")).toBe("1-hour");
    expect(windowAdjective("primary")).toBe("primary");
    // A limit says the status line's own words.
    const parked = decodeAccount({ ...WORK_ROW, status: "limit", usage: undefined, resets_at: RESET })!;
    expect(accountUsageWords(parked)).toBe(`Limit reached · ${resetWords(RESET)}`);
  });
});

/* ------------------------------------------------------- the session */

describe("the coding session", () => {
  it("a wait armed by itself reads as one, with Cancel and Choose account", async () => {
    api.gets["/build/sessions/s1"] = view({ account_limit: LIMIT_RAW, account_wait: AUTO_WAIT });
    renderSession();
    const card = await screen.findByTestId("account-limit-card");
    expect(within(card).getByTestId("account-limit-waiting").textContent).toBe(
      `Waiting for Work's reset at ${localTime(RESET)}; it carries on by itself.`,
    );
    expect(within(card).queryByTestId("account-limit-resume")).toBeNull();
    expect(within(card).getByTestId("account-limit-cancel")).toBeInTheDocument();
    expect(within(card).getByTestId("account-limit-choose")).toBeInTheDocument();
  });

  it("Choose account while it waits continues on the account picked (the Choose account path)", async () => {
    api.gets["/build/sessions/s1"] = view({ account_limit: LIMIT_RAW, account_wait: AUTO_WAIT });
    api.postAnswers["POST /build/sessions/s1/account"] = {
      session: view({ running: true, turn_id: "bt_9", account: SPARE_ROW }),
      turn_id: "bt_9",
    };
    fetchMock.mockImplementation(async () => sse([["done", { reply: "Carried on." }]]));
    renderSession();
    fireEvent.click(await screen.findByTestId("account-limit-choose"));
    fireEvent.click(await screen.findByTestId("account-pick-spare"));
    await waitFor(() =>
      expect(api.calls).toContainEqual({
        method: "POST",
        path: "/build/sessions/s1/account",
        body: { account: "spare", continue: true },
      }),
    );
  });

  it("a reply carried on with another account of the group names both accounts", async () => {
    const move = { from: "work", fromName: "TSI", to: "spare", toName: "Spare", group: "Work" };
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { role: "user", content: "Write the report", turnId: "t1" },
        { role: "assistant", content: "Started.", turnId: "t1", accountLimit: LIMIT_RAW, error: "parked" },
        { role: "user", content: "Carry on.", continuation: true, turnId: "t2", accountMove: move },
        { role: "assistant", content: "Finished the report.", turnId: "t2", accountMove: move },
      ],
    });
    renderSession();
    expect(await screen.findByText("Finished the report.")).toBeInTheDocument();
    const lines = screen.getAllByTestId("harness-account-move");
    expect(lines).toHaveLength(1);
    expect(lines[0].textContent).toBe("TSI reached its limit; carried on with Spare (same group).");
    expect(screen.queryByText("Carry on.")).toBeNull();
  });

  it("the account chip says the account's current usage window", async () => {
    renderSession();
    const usage = await screen.findByTestId("account-chip-usage");
    expect(usage.textContent).toBe(`63% of the 5-hour window, ${resetWords(WINDOW_RESET)}`);
    expect(screen.getByTestId("account-chip").getAttribute("title")).toContain(
      `TSI · 63% of the 5-hour window, ${resetWords(WINDOW_RESET)}`,
    );
  });

  it("an account the vendor has not reported on says so, never 0%", async () => {
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "claude-cli", account: "spare" }, account: SPARE_ROW });
    renderSession();
    const usage = await screen.findByTestId("account-chip-usage");
    await waitFor(() => expect(screen.getByTestId("account-chip-name").textContent).toBe("Spare"));
    expect(usage.textContent).toBe("usage not known yet");
  });

  it("a turn's end reads the account list again (its usage moved)", async () => {
    renderSession();
    await screen.findByTestId("account-chip-usage");
    const reads = () => api.calls.filter((c) => c.method === "GET" && c.path === "/build/accounts").length;
    await waitFor(() => expect(reads()).toBe(1));
    api.gets["/build/accounts"] = {
      ...ACCOUNTS,
      claude: [{ ...WORK_ROW, usage: { ...WORK_ROW.usage, percent: 71 } }, SPARE_ROW],
    };
    const input = screen.getByTestId("harness-input") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "go on" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("account-chip-usage").textContent).toMatch(/^71% of the 5-hour/));
    expect(reads()).toBeGreaterThanOrEqual(2);
  });
});

/* -------------------------------------------------------- the Timeline */

describe("the Timeline's header", () => {
  const timelineView = { id: "s1", messages: [], setup: { provider: "claude-cli" } } as unknown as BuildSessionView;

  it("says the account and its usage window", () => {
    render(<HarnessTimeline view={timelineView} live={null} models={[]} account={decodeAccount(WORK_ROW)} />);
    expect(screen.getByTestId("timeline-account-usage").textContent).toBe(
      `TSI · 63% of the 5-hour window, ${resetWords(WINDOW_RESET)}`,
    );
  });

  it("says usage not known yet for an account with no numbers, and nothing without an account", () => {
    const { unmount } = render(
      <HarnessTimeline view={timelineView} live={null} models={[]} account={decodeAccount(SPARE_ROW)} />,
    );
    expect(screen.getByTestId("timeline-account-usage").textContent).toBe("Spare · usage not known yet");
    unmount();
    render(<HarnessTimeline view={timelineView} live={null} models={[]} />);
    expect(screen.queryByTestId("timeline-account-usage")).toBeNull();
  });
});

/* ------------------------------------------------------------ the bell */

describe("the bell", () => {
  const ev = (type: string, payload: Record<string, unknown>) =>
    ({ id: "e1", ts: "2030-01-01T15:00:00Z", type, payload, session_id: "build:s1" }) as never;

  it("a limit the reply waits out by itself is news, not a question", () => {
    const waiting = toActivity(
      ev("build.account_limit", { thread_id: "s1", title: "Add a cart", account_name: "Work", kind: "usage",
        resets_at: RESET, waiting: true }),
    )!;
    expect(waiting.title).toBe("The Work account reached its limit");
    expect(waiting.attention).toBeUndefined();
    expect(waiting.body).toContain("it carries on by itself");
    expect(waiting.body.startsWith("Add a cart · ")).toBe(true);
    // Not waiting (the setting is off): it asks the user, as before.
    const asking = toActivity(
      ev("build.account_limit", { thread_id: "s1", title: "Add a cart", account_name: "Work", kind: "usage",
        waiting: false }),
    )!;
    expect(asking.attention).toBe(true);
    expect(asking.body).toBe("Add a cart");
  });

  it("a move is ONE row naming both accounts", () => {
    const row = toActivity(
      ev("build.resumed", { thread_id: "s1", title: "Add a cart", account: "spare", account_name: "Spare",
        moved_from: "work", moved_from_name: "TSI", group: "Work", turn_id: "bt_1" }),
    )!;
    expect(row.title).toBe("TSI reached its limit; carried on with Spare (same group)");
    expect(row.attention).toBeUndefined();
    const plain = toActivity(ev("build.resumed", { thread_id: "s1", title: "Add a cart", account_name: "Work" }))!;
    expect(plain.title).toBe("A Build session resumed on the Work account");
  });
});
