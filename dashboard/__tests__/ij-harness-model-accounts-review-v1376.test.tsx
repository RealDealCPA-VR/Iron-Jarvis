/**
 * v1.376.0 REVIEW — the IJ harness's model menu (one group per company, the
 * Iron-Proxy accounts in it). Each block below is a defect the review found in
 * the first cut, driven through the real session screen
 * (components/harness/HarnessSession) and the real menu
 * (components/chat/ModelMenu `companies`, lib/harnessModelMenu), transport
 * mocked exactly as in ij-harness-model-accounts-v1376.test.tsx:
 *
 *  1. A FOLDED pick keeps its reasoning chip. A keyless Anthropic's rows are
 *     listed under Claude Code; picking one stores `claude-cli::<id>`, which
 *     GET /models never lists under claude-cli — so the composer found no row
 *     for the pick and the reasoning chip vanished (a level set before stayed
 *     on the turn, unseen). The folded row runs on the CLI, which takes every
 *     level (providers/reasoning: claude-cli and codex-cli offer all three).
 *  2. A session on a provider with NO model of its own (an account chosen
 *     from another vendor) runs that provider's own default. The chip said
 *     "Your Claude plan · Work"; the menu marked "default model" — the APP's
 *     default — and nothing under the account.
 *  3. "The session's own account is always listed and marked" — except when
 *     its vendor's list was empty (its only account turned off): then the way
 *     was a plain "Claude Code" row, as if this PC's own sign-in served it.
 *  4. A window over 100% used read "Near its limit".
 *  5. Escape on a row of the harness menu (the phone sheet is all rows) did
 *     nothing, and closing never handed focus back to the chip.
 */
import React from "react";
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
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    answers: {} as Record<string, unknown | ((path: string, body: unknown) => unknown)>,
    models: [] as Record<string, unknown>[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.answers[`${method} ${path}`];
    const value = typeof hit === "function" ? (hit as (p: string, b: unknown) => unknown)(path, body) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return value instanceof Promise ? value : Promise.resolve(value ?? {});
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
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: api.models, usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession, ACCOUNTS_POLL_MS } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { AccountCard } from "@/components/harness/HarnessAccounts";
import { accountMenuState, decodeAccount, decodeAccounts } from "@/lib/buildAccounts";

/* ------------------------------------------------------------- fixtures */

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

const LEVELS = ["low", "medium", "high"];
/** GET /models as the daemon answers it (tests/test_harness_model_fold_v1376
 *  reads the real one): every claude-cli / codex-cli row offers the three
 *  levels; a keyless Anthropic's curated rows do too; a keyless OpenAI's
 *  gpt-4o offers none (the API's own table). */
const CLAUDE = [
  { provider: "claude-cli", model: "subscription", available: true, kind: "cli", inherited_from: null, reasoning: LEVELS, reasoning_default: "" },
  { provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5", available: true, kind: "cli", inherited_from: null, reasoning: LEVELS, reasoning_default: "" },
];
const ANTHROPIC_SERVED = [
  { provider: "anthropic", model: "claude-opus-5-5", label: "Opus 5.5", available: true, kind: "api", inherited_from: "claude-cli", reasoning: LEVELS, reasoning_default: "" },
  { provider: "anthropic", model: "claude-opus-4-8", available: true, kind: "api", inherited_from: "claude-cli", reasoning: LEVELS, reasoning_default: "" },
];
const CODEX = [
  { provider: "codex-cli", model: "subscription", available: true, kind: "cli", inherited_from: null, reasoning: LEVELS, reasoning_default: "" },
];
const OPENAI_SERVED = [
  { provider: "openai", model: "gpt-4o", available: true, kind: "api", inherited_from: "codex-cli", reasoning: [], reasoning_default: "" },
];

const acct = (over: Record<string, unknown>) => ({
  vendor: "claude",
  vendor_label: "Claude Code",
  provider: "claude-cli",
  default: false,
  plan: null,
  plan_label: "",
  ...over,
});
const WORK = acct({ id: "work", name: "Work", default: true, plan: "max", plan_label: "Max", status: "ready" });
const PERSONAL = acct({ id: "personal", name: "Personal", status: "ready" });
const PROXY_ON = { enabled: true, running: true, needs_update: false, note: "" };

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-10-23T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}

function onWork(setup: Record<string, unknown>, over: Record<string, unknown> = {}) {
  return view({
    setup: { account: "work", account_name: "Work", ...setup },
    account: WORK,
    account_vendor: "claude",
    account_limit: null,
    account_wait: null,
    ...over,
  });
}

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1": onWork({ provider: "claude-cli", model: "claude-opus-5-5" }),
    "/build/accounts": { claude: [WORK, PERSONAL], codex: [], iron_proxy: PROXY_ON },
  };
  api.calls = [];
  api.answers = {};
  api.models = [...CLAUDE, ...ANTHROPIC_SERVED, ...CODEX, ...OPENAI_SERVED];
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

async function openMenu(waitForAccount = "work"): Promise<HTMLElement> {
  const chip = await screen.findByTitle("Switch model");
  if (waitForAccount) await screen.findByTestId("account-chip");
  fireEvent.click(chip);
  const menu = await screen.findByTestId("model-menu");
  if (waitForAccount) await within(menu).findByTestId(`model-account-${waitForAccount}`);
  return menu;
}

const levelOptions = () =>
  Array.from((screen.getByTestId("reasoning-level") as HTMLSelectElement).options).filter((o) => !o.hidden).map((o) => o.value);

/* ------------------------------------------------- 1. folded pick: reasoning */

describe("a folded pick keeps its reasoning chip", () => {
  it("a Claude id only the keyless Anthropic lists, stored as Claude Code's, offers the CLI's levels", async () => {
    api.gets["/build/sessions/s1"] = onWork({ provider: "claude-cli", model: "claude-opus-4-8" });
    renderSession();
    await waitFor(() => expect(screen.getByTitle("Switch model").textContent).toBe("Opus 4.8 · Work"));
    await waitFor(() => expect(screen.getByTestId("reasoning-level")).toBeTruthy());
    // v1.401.2 (moved on purpose): the levels only, no "Default" row.
    expect(levelOptions()).toEqual(LEVELS);
  });

  it("an OpenAI id a keyless OpenAI lists, folded under Codex, offers Codex's levels (the CLI applies them)", async () => {
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "codex-cli", model: "gpt-4o" } });
    api.gets["/build/accounts"] = { claude: [], codex: [], iron_proxy: PROXY_ON };
    renderSession();
    await waitFor(() => expect(screen.getByTestId("reasoning-level")).toBeTruthy());
    // v1.401.2 (moved on purpose): the levels only, no "Default" row.
    expect(levelOptions()).toEqual(LEVELS);
  });

  it("a session stored before the change as anthropic::<id> keeps its chip and is marked under its account", async () => {
    api.gets["/build/sessions/s1"] = onWork({ provider: "anthropic", model: "claude-opus-4-8" });
    renderSession();
    await waitFor(() => expect(screen.getByTestId("reasoning-level")).toBeTruthy());
    const menu = await openMenu();
    fireEvent.click(within(menu).getByTestId("model-account-work"));
    const sub = await screen.findByTestId("model-sub");
    expect(within(sub).getByText("Opus 4.8").closest("button")?.className).toContain("text-accent-soft");
    expect(within(menu).getByText("default model").className).not.toContain("text-accent-soft");
  });
});

/* -------------------------------- 2. a provider with no model of its own */

describe("a session on its provider's own default", () => {
  it("the menu marks that provider's plan row under the account, never the app's default model", async () => {
    api.gets["/build/sessions/s1"] = onWork({ provider: "claude-cli" });
    renderSession();
    await waitFor(() => expect(screen.getByTitle("Switch model").textContent).toBe("Your Claude plan · Work"));
    const menu = await openMenu();
    expect(within(menu).getByText("default model").className).not.toContain("text-accent-soft");
    fireEvent.click(within(menu).getByTestId("model-account-work"));
    const sub = await screen.findByTestId("model-sub");
    const plan = within(sub).getByText("Your Claude plan").closest("button") as HTMLElement;
    expect(plan.className).toContain("text-accent-soft");
    // …and the reasoning chip is that row's (the CLI's levels), not the
    // app default's (the demo model offers none).
    // v1.401.2 (moved on purpose): the levels only, no "Default" row.
    expect(levelOptions()).toEqual(LEVELS);
  });

  it("a provider with no such row marks nothing, still never the app's default model", async () => {
    api.models = [...CLAUDE, { provider: "ollama", model: "qwen3:8b", available: true, kind: "local", on_this_network: true }];
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "ollama" } });
    api.gets["/build/accounts"] = { claude: [], codex: [], iron_proxy: PROXY_ON };
    renderSession();
    await screen.findByTitle("Switch model");
    fireEvent.click(screen.getByTitle("Switch model"));
    const menu = await screen.findByTestId("model-menu");
    await within(menu).findByText("Ollama");
    expect(within(menu).getByText("default model").className).not.toContain("text-accent-soft");
  });
});

/* -------------------------------- 3. the session's own account, alone */

describe("the session's own account is listed even when its vendor's list is empty", () => {
  it("its only account turned off (the list leaves it out): marked under Claude Code with its state", async () => {
    api.gets["/build/accounts"] = { claude: [], codex: [], iron_proxy: PROXY_ON };
    api.gets["/build/sessions/s1"] = onWork(
      { provider: "claude-cli", model: "claude-opus-5-5" },
      { account: { ...WORK, status: "off" } },
    );
    renderSession();
    await screen.findByTestId("account-chip");
    fireEvent.click(screen.getByTitle("Switch model"));
    const menu = await screen.findByTestId("model-menu");
    const work = await within(menu).findByTestId("model-account-work");
    expect(work.getAttribute("data-current")).toBe("true");
    expect(within(work).getByTestId("model-account-state").textContent).toBe("Turned off");
    // The way is a heading over that account, not a row of its own.
    expect(within(menu).getByTestId("model-way-claude-cli").tagName).toBe("DIV");
  });

  it("Iron-Proxy turned off: the account is still the session's, and the menu says why its turns stop", async () => {
    const OFF_NOTE = "Iron-Proxy is off; turn it on in Connections to run Build sessions on your accounts.";
    api.gets["/build/accounts"] = {
      claude: [],
      codex: [],
      iron_proxy: { enabled: false, running: false, needs_update: false, note: OFF_NOTE },
    };
    // The daemon's view of a session's account while Iron-Proxy is off: its
    // name, no status (build_accounts.session_account).
    api.gets["/build/sessions/s1"] = onWork(
      { provider: "claude-cli", model: "claude-opus-5-5" },
      { account: { id: "work", name: "Work", status: null } },
    );
    renderSession();
    await waitFor(() => expect(screen.getByTitle("Switch model").textContent).toBe("Opus 5.5 · Work"));
    fireEvent.click(screen.getByTitle("Switch model"));
    const menu = await screen.findByTestId("model-menu");
    const way = await within(menu).findByTestId("model-way-claude-cli");
    expect(within(way).getByTestId("model-account-work").getAttribute("data-current")).toBe("true");
    expect(way).toHaveTextContent(OFF_NOTE);
    // A model under it is the model alone: the account stays the session's.
    api.answers["PATCH /build/sessions/s1"] = () => onWork({ provider: "claude-cli", model: "claude-opus-4-8" });
    fireEvent.click(within(way).getByTestId("model-account-work"));
    fireEvent.click(within(await screen.findByTestId("model-sub")).getByText("Opus 4.8"));
    await waitFor(() =>
      expect(api.calls.filter((c) => c.method === "PATCH")).toEqual([
        { method: "PATCH", path: "/build/sessions/s1", body: { provider: "claude-cli", model: "claude-opus-4-8" } },
      ]),
    );
  });
});

/* ------------------------------------------------- 4. over 100% used */

describe("a window over its limit", () => {
  it("never reads 'Near its limit'", () => {
    const view = decodeAccounts({
      claude: [acct({ id: "o", name: "Over", status: "usage", usage: { percent: 112, window: "5 hours", window_mins: 300, resets_at: null } })],
      codex: [],
      iron_proxy: PROXY_ON,
    });
    const s = accountMenuState(view!.groups.claude[0]);
    expect(s.text).not.toMatch(/near/i);
    // The decoder caps the vendor's percent at 100.
    expect(s.text).toBe("At its limit · 100% used · 5 hours");
    expect(s.warn).toBe(true);
    // 99% is still near.
    const near = decodeAccounts({
      claude: [acct({ id: "n", name: "N", status: "usage", usage: { percent: 99, window: "5 hours", window_mins: 300, resets_at: null } })],
      codex: [],
      iron_proxy: PROXY_ON,
    });
    expect(accountMenuState(near!.groups.claude[0]).text).toBe("Near its limit · 99% used · 5 hours");
  });
});

/* ------------------------------- the card and the menu share one 90% */

describe("the account card's warning bar and the menu's 'Near its limit' turn at the same percent", () => {
  // AccountCard's bar used a literal 90 and now reads NEAR_LIMIT_PERCENT;
  // nothing pinned the card's boundary, so moving the shared number would
  // have changed the card silently. 89% is the accent and plain words; 90%
  // is the warning tone in both.
  for (const [pct, warn] of [[89, false], [90, true]] as const) {
    it(`${pct}%`, () => {
      const a = decodeAccount(
        acct({ id: "u", name: "Usage", status: "usage", usage: { percent: pct, window: "5 hours", window_mins: 300, resets_at: null } }),
      )!;
      render(<AccountCard account={a} current={false} />);
      const bar = screen.getByRole("meter").firstElementChild as HTMLElement;
      expect(bar.className.includes("bg-tone-warn"), "card bar").toBe(warn);
      expect(bar.className.includes("bg-accent"), "card bar").toBe(!warn);
      const s = accountMenuState(a);
      expect(s.kind).toBe(warn ? "near" : "usage");
      expect(s.text).toBe(warn ? `Near its limit · ${pct}% used · 5 hours` : `${pct}% used · 5 hours`);
      expect(s.warn).toBe(warn);
    });
  }
});

/* ------------------------------------------------- 5. Escape and focus */

describe("Escape closes the harness menu from any row and hands focus back to the chip", () => {
  it("from an account row", async () => {
    renderSession();
    const menu = await openMenu();
    const row = within(menu).getByTestId("model-account-personal");
    row.focus();
    fireEvent.keyDown(row, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("model-menu")).toBeNull());
    expect(document.activeElement).toBe(screen.getByTitle("Switch model"));
  });

  it("from the filter box", async () => {
    renderSession();
    await openMenu();
    fireEvent.keyDown(screen.getByTestId("model-filter"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("model-menu")).toBeNull());
    expect(document.activeElement).toBe(screen.getByTitle("Switch model"));
  });
});

/* ------------------------------------------------- the 5 s re-read */

describe("the account list is re-read only while the menu is open", () => {
  it("every few seconds while open; never once it closes", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    renderSession();
    await screen.findByTestId("account-chip");
    const reads = () => api.calls.filter((c) => c.method === "GET" && c.path === "/build/accounts").length;
    await waitFor(() => expect(reads()).toBe(1));
    fireEvent.click(screen.getByTitle("Switch model"));
    await screen.findByTestId("model-menu");
    await waitFor(() => expect(reads()).toBe(2)); // read again on open
    await act(async () => {
      vi.advanceTimersByTime(ACCOUNTS_POLL_MS * 2);
    });
    await waitFor(() => expect(reads()).toBe(4));
    fireEvent.click(screen.getByTitle("Switch model")); // closes it
    await waitFor(() => expect(screen.queryByTestId("model-menu")).toBeNull());
    await act(async () => {
      vi.advanceTimersByTime(ACCOUNTS_POLL_MS * 3);
    });
    expect(reads()).toBe(4);
  });
});
