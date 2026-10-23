/**
 * v1.376.0 — the IJ harness's MODEL MENU: one group per COMPANY, and the
 * Iron-Proxy accounts in it. The user (2026-10-23): "In the model selection
 * for the IJ harness, if the user has Iron-proxy in use it should list which
 * claude code account the user wants to engage. Also there is an anthropic
 * and a claude code in the same list, but those are the same company."
 *
 * Driven through the real session screen (components/harness/HarnessSession)
 * and the real menu (components/chat/ModelMenu `companies`, built by
 * lib/harnessModelMenu), with the transport mocked:
 *
 *  - Claude Code and the Anthropic API sit in ONE "Claude (Anthropic)" group,
 *    Codex and the OpenAI API in ONE "OpenAI" group, each way labelled with
 *    how it is paid for; every other provider stays a row of its own;
 *  - a keyless Anthropic served through the signed-in CLI is listed once,
 *    under Claude Code; a real API key keeps its pay-per-use way;
 *  - with Iron-Proxy accounts, the Claude Code way lists ONE ENTRY PER
 *    ACCOUNT — name, plan, state in words with a second cue — the session's
 *    own marked; one that cannot serve is shown and cannot be picked;
 *  - a model picked under another account sends the account AND the model in
 *    ONE request, and both the model chip and the account chip show it; a
 *    refusal changes nothing and says the daemon's sentence;
 *  - no account list (Iron-Proxy off, not read yet, a 404): Claude Code is
 *    the one row it always was;
 *  - the model chip names the account, inside the box at 390 px;
 *  - the typed filter and Recent name the account a row runs on.
 *
 * The chat page's menu (no `companies`) is pinned byte for byte in
 * chat-model-menu-unchanged-v1376.test.tsx.
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

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { resetWords } from "@/lib/buildAccounts";
import { RECENT_MODELS_KEY } from "@/lib/recentModels";

/* ------------------------------------------------------------- fixtures */

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

const RESET = "2030-01-01T16:12:00Z";

const CLAUDE = [
  { provider: "claude-cli", model: "subscription", available: true, kind: "cli", inherited_from: null },
  { provider: "claude-cli", model: "claude-opus-4-8", label: "Opus 4.8", available: true, kind: "cli", inherited_from: null },
  { provider: "claude-cli", model: "claude-sonnet-4-6", label: "Sonnet 4.6", available: true, kind: "cli", inherited_from: null },
];
/** A keyless Anthropic, served by the signed-in Claude Code (the same rows,
 *  plus one curated id the picker does not list). */
const ANTHROPIC_SERVED = [
  { provider: "anthropic", model: "claude-opus-4-8", label: "Opus 4.8", available: true, kind: "api", inherited_from: "claude-cli" },
  { provider: "anthropic", model: "claude-sonnet-4-6", label: "Sonnet 4.6", available: true, kind: "api", inherited_from: "claude-cli" },
  { provider: "anthropic", model: "claude-haiku-4-5", available: true, kind: "api", inherited_from: "claude-cli" },
];
/** An Anthropic API KEY: pay per use, its own way. */
const ANTHROPIC_KEY = [
  { provider: "anthropic", model: "claude-opus-4-8", label: "Opus 4.8", available: true, kind: "api", inherited_from: null },
];
const CODEX = [
  { provider: "codex-cli", model: "subscription", available: true, kind: "cli", inherited_from: null },
  { provider: "codex-cli", model: "gpt-5.5", available: true, kind: "cli", inherited_from: null },
];
const OPENAI_SERVED = [{ provider: "openai", model: "gpt-5.5", available: true, kind: "api", inherited_from: "codex-cli" }];
const OPENAI_KEY = [{ provider: "openai", model: "gpt-5.5", available: true, kind: "api", inherited_from: null }];
const OLLAMA = [{ provider: "ollama", model: "qwen3:8b", available: true, kind: "local", on_this_network: true, size_b: 8 }];

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
const PERSONAL = acct({
  id: "personal", name: "Personal", plan: "pro", plan_label: "Pro", status: "usage",
  usage: { percent: 38, window: "7 days", window_mins: 10080, resets_at: RESET },
});
const NEAR = acct({
  id: "near", name: "Near", plan: "pro", plan_label: "Pro", status: "usage",
  usage: { percent: 93, window: "5 hours", window_mins: 300, resets_at: RESET },
});
const CAPPED = acct({ id: "capped", name: "Capped", status: "limit", resets_at: RESET, limit_kind: "usage" });
const SIDE = acct({ id: "side", name: "Side", plan: "pro", plan_label: "Pro", status: "needs_sign_in" });
const SHUT = acct({ id: "shut", name: "Shut", status: "off" });
const CX = {
  id: "cx", name: "Codex Plus", vendor: "codex", vendor_label: "Codex", provider: "codex-cli",
  default: true, plan: "plus", plan_label: "Plus", status: "ready",
};
const PROXY_ON = { enabled: true, running: true, needs_update: false, note: "" };
const PROXY_OFF = {
  enabled: false, running: false, needs_update: false,
  note: "Iron-Proxy is off; turn it on in Connections to run Build sessions on your accounts.",
};

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

/** A session on the Work account, on Opus 4.8. */
function onWork(over: Record<string, unknown> = {}) {
  return view({
    setup: { provider: "claude-cli", model: "claude-opus-4-8", account: "work", account_name: "Work" },
    account: WORK,
    account_vendor: "claude",
    account_limit: null,
    account_wait: null,
    ...over,
  });
}

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1": onWork(),
    "/build/accounts": { claude: [WORK, PERSONAL, NEAR, CAPPED, SIDE], codex: [CX], iron_proxy: PROXY_ON },
  };
  api.calls = [];
  api.answers = {};
  api.models = [...CLAUDE, ...ANTHROPIC_SERVED, ...CODEX, ...OPENAI_KEY, ...OLLAMA];
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
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

/** Open the model menu once the session (and, when it has them, its
 *  accounts) are on screen. */
async function openMenu(waitForAccount = "work"): Promise<HTMLElement> {
  const chip = await screen.findByTitle("Switch model");
  if (waitForAccount) await screen.findByTestId("account-chip");
  fireEvent.click(chip);
  const menu = await screen.findByTestId("model-menu");
  if (waitForAccount) await within(menu).findByTestId(`model-account-${waitForAccount}`);
  return menu;
}

const group = (menu: HTMLElement, id: string): HTMLElement => {
  const g = menu.querySelector(`[data-testid="model-company"][data-company="${id}"]`);
  if (!g) throw new Error(`no ${id} group in the menu`);
  return g as HTMLElement;
};

const patches = () => api.calls.filter((c) => c.method === "PATCH");

/* ---------------------------------------------------------- the groups */

describe("one group per company", () => {
  it("Claude Code and the Anthropic API under Claude (Anthropic); Codex and the OpenAI API under OpenAI; the rest as before", async () => {
    api.gets["/build/accounts"] = { claude: [], codex: [], iron_proxy: PROXY_OFF };
    api.models = [...CLAUDE, ...ANTHROPIC_KEY, ...CODEX, ...OPENAI_KEY, ...OLLAMA];
    renderSession();
    const menu = await openMenu("");
    const claude = await waitFor(() => group(menu, "claude"));
    expect(claude.getAttribute("aria-label")).toBe("Claude (Anthropic)");
    expect(claude).toHaveTextContent(/^Claude \(Anthropic\)/);
    // The ways, each saying how it is paid for (KIND_BADGE's words).
    const cc = within(claude).getByTestId("model-way-claude-cli");
    expect(cc).toHaveTextContent("Claude Code");
    expect(within(cc).getByTestId("model-kind-badge").textContent).toBe("cloud · on your plan");
    const api1 = within(claude).getByTestId("model-way-anthropic");
    expect(api1).toHaveTextContent("Anthropic API");
    expect(within(api1).getByTestId("model-kind-badge").textContent).toBe("cloud · pay per use");
    const openai = group(menu, "openai");
    expect(openai.getAttribute("aria-label")).toBe("OpenAI");
    expect(within(openai).getByTestId("model-way-codex-cli")).toHaveTextContent("Codexcloud · on your plan");
    expect(within(openai).getByTestId("model-way-openai")).toHaveTextContent("OpenAI APIcloud · pay per use");
    // Exactly two company groups; Ollama is a provider row of its own,
    // first (local first, as the chat orders them).
    expect(menu.querySelectorAll('[data-testid="model-company"]')).toHaveLength(2);
    const ollama = within(menu).getByText("Ollama");
    expect(claude.contains(ollama)).toBe(false);
    expect(ollama.compareDocumentPosition(claude) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Within a group: the plan's way before the pay-per-use one.
    expect(cc.compareDocumentPosition(api1) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("no duplicate", () => {
  it("a keyless Anthropic served by Claude Code is listed once, under Claude Code", async () => {
    api.gets["/build/accounts"] = { claude: [], codex: [], iron_proxy: PROXY_OFF };
    api.models = [...CLAUDE, ...ANTHROPIC_SERVED, ...CODEX, ...OPENAI_SERVED];
    renderSession();
    const menu = await openMenu("");
    const claude = await waitFor(() => group(menu, "claude"));
    // No Anthropic way at all: it IS Claude Code.
    expect(within(claude).queryByTestId("model-way-anthropic")).toBeNull();
    expect(within(group(menu, "openai")).queryByTestId("model-way-openai")).toBeNull();
    expect(within(menu).queryByText(/^Anthropic$/)).toBeNull();
    fireEvent.click(within(claude).getByTestId("model-way-claude-cli"));
    const sub = await screen.findByTestId("model-sub");
    const names = within(sub).getAllByRole("button").map((b) => b.textContent);
    // Opus 4.8 and Sonnet 4.6 once each; the curated id only Anthropic
    // listed is there too, under Claude Code.
    expect(names.filter((n) => n?.startsWith("Opus 4.8"))).toHaveLength(1);
    expect(names.filter((n) => n?.startsWith("Sonnet 4.6"))).toHaveLength(1);
    expect(names.some((n) => n?.startsWith("Haiku 4.5"))).toBe(true);
    // The typed filter has no twin either.
    fireEvent.change(screen.getByTestId("model-filter"), { target: { value: "opus" } });
    const rows = await screen.findAllByTestId("model-match");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Claude Code");
  });

  it("a pick of the folded curated model runs it through Claude Code", async () => {
    api.gets["/build/accounts"] = { claude: [], codex: [], iron_proxy: PROXY_OFF };
    api.models = [...CLAUDE, ...ANTHROPIC_SERVED];
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "ollama", model: "qwen3:8b" } });
    api.answers["PATCH /build/sessions/s1"] = () => view({ setup: { provider: "claude-cli", model: "claude-haiku-4-5" } });
    renderSession();
    const menu = await openMenu("");
    fireEvent.click(await within(menu).findByTestId("model-way-claude-cli"));
    fireEvent.click(within(await screen.findByTestId("model-sub")).getByText("Haiku 4.5"));
    await waitFor(() =>
      expect(patches()).toEqual([
        { method: "PATCH", path: "/build/sessions/s1", body: { provider: "claude-cli", model: "claude-haiku-4-5" } },
      ]),
    );
  });

  it("a real API key keeps its own pay-per-use row, and a pick there is the API's", async () => {
    api.gets["/build/accounts"] = { claude: [WORK], codex: [], iron_proxy: PROXY_ON };
    api.models = [...CLAUDE, ...ANTHROPIC_KEY];
    api.answers["PATCH /build/sessions/s1"] = () => view({ setup: { provider: "anthropic", model: "claude-opus-4-8" } });
    renderSession();
    const menu = await openMenu();
    const way = within(group(menu, "claude")).getByTestId("model-way-anthropic");
    expect(way).toHaveTextContent("Anthropic API");
    expect(within(way).getByTestId("model-kind-badge").textContent).toBe("cloud · pay per use");
    fireEvent.click(way);
    fireEvent.click(within(await screen.findByTestId("model-sub")).getByText("Opus 4.8"));
    await waitFor(() =>
      expect(patches()).toEqual([
        { method: "PATCH", path: "/build/sessions/s1", body: { provider: "anthropic", model: "claude-opus-4-8" } },
      ]),
    );
  });
});

/* ------------------------------------------------------------- accounts */

describe("the Iron-Proxy accounts in the Claude group", () => {
  it("one entry per account: name, plan, state in words with a second cue; the session's own marked", async () => {
    renderSession();
    const menu = await openMenu();
    const claude = group(menu, "claude");
    const way = within(claude).getByTestId("model-way-claude-cli");
    // The way is a heading over its accounts, with how it is paid for.
    expect(way.tagName).toBe("DIV");
    expect(within(way).getByTestId("model-kind-badge").textContent).toBe("cloud · on your plan");
    const ids = within(way)
      .getAllByRole("button")
      .map((b) => b.getAttribute("data-testid"));
    expect(ids).toEqual([
      "model-account-work",
      "model-account-personal",
      "model-account-near",
      "model-account-capped",
      "model-account-side",
    ]);
    const row = (id: string) => within(way).getByTestId(`model-account-${id}`);
    const state = (id: string) => within(row(id)).getByTestId("model-account-state");

    // Work: the session's own — marked by a check (and its words), Ready.
    expect(row("work").getAttribute("data-current")).toBe("true");
    expect(within(row("work")).getByTestId("model-account-current")).toBeTruthy();
    expect(row("work")).toHaveTextContent("Work· Max (this session's account)Ready");
    expect(state("work").getAttribute("data-kind")).toBe("ready");
    expect(within(state("work")).queryByTestId("model-account-warn")).toBeNull();

    // Personal: a percent the vendor sent, with a meter (a shape, not a hue).
    expect(row("personal").getAttribute("data-current")).toBeNull();
    expect(state("personal").textContent).toBe("38% used · 7 days");
    expect(within(state("personal")).getByTestId("model-account-meter")).toBeTruthy();
    expect(within(state("personal")).queryByTestId("model-account-warn")).toBeNull();

    // Near: near its limit, with the percent — a mark AND the meter.
    expect(state("near").textContent).toBe("Near its limit · 93% used · 5 hours");
    expect(state("near").getAttribute("data-kind")).toBe("near");
    expect(within(state("near")).getByTestId("model-account-warn")).toBeTruthy();
    expect(within(state("near")).getByTestId("model-account-meter")).toBeTruthy();

    // Capped: at its limit, with when it resets — and still pickable (the
    // chip's list allows it; the next turn shows the limit card).
    expect(state("capped").textContent).toBe(`Limit reached · ${resetWords(RESET)}`);
    expect(within(state("capped")).getByTestId("model-account-warn")).toBeTruthy();
    expect((row("capped") as HTMLButtonElement).disabled).toBe(false);

    // Side: signed out — shown, not pickable, with its reason.
    expect(state("side").textContent).toBe("Needs to sign in again");
    expect(within(state("side")).getByTestId("model-account-warn")).toBeTruthy();
    expect((row("side") as HTMLButtonElement).disabled).toBe(true);
    expect(row("side").getAttribute("title")).toBe(
      "Needs to sign in again. Sign it in again on the Connections page to use it.",
    );

    // The warning states are not told by colour alone: each has the mark.
    for (const id of ["near", "capped", "side"]) {
      expect(state(id).className).toContain("text-tone-warn");
      expect(within(state(id)).getByTestId("model-account-warn")).toBeTruthy();
    }

    // Codex accounts the same way, under OpenAI.
    const codex = within(group(menu, "openai")).getByTestId("model-account-cx");
    expect(codex).toHaveTextContent("Codex Plus· PlusReady");
  });

  it("opening the menu reads the account list again, so each state is the one now", async () => {
    renderSession();
    await screen.findByTestId("account-chip");
    const reads = () => api.calls.filter((c) => c.method === "GET" && c.path === "/build/accounts").length;
    await waitFor(() => expect(reads()).toBe(1));
    // Personal reached its limit since the session opened.
    api.gets["/build/accounts"] = {
      claude: [WORK, { ...PERSONAL, status: "limit", usage: undefined, resets_at: RESET, limit_kind: "usage" }],
      codex: [],
      iron_proxy: PROXY_ON,
    };
    fireEvent.click(screen.getByTitle("Switch model"));
    const row = await screen.findByTestId("model-account-personal");
    await waitFor(() =>
      expect(within(row).getByTestId("model-account-state").textContent).toBe(`Limit reached · ${resetWords(RESET)}`),
    );
    expect(reads()).toBeGreaterThanOrEqual(2); // read again on open (and every few seconds while open)
  });

  it("an Iron Jarvis with no account list (a 404) is not asked again when the menu opens", async () => {
    api.gets["/build/accounts"] = new api.ApiError("no such route", 404);
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "claude-cli", model: "claude-opus-4-8" } });
    renderSession();
    const reads = () => api.calls.filter((c) => c.method === "GET" && c.path === "/build/accounts").length;
    await waitFor(() => expect(reads()).toBe(1));
    await openMenu("");
    expect(reads()).toBe(1);
  });

  it("a turned-off account is shown and cannot be picked; the session's own removed account stays listed and marked", async () => {
    api.gets["/build/accounts"] = { claude: [PERSONAL, SHUT], codex: [], iron_proxy: PROXY_ON };
    api.gets["/build/sessions/s1"] = onWork({ account: { id: "work", name: "Work", status: "missing" } });
    renderSession();
    const menu = await openMenu("personal");
    const shut = within(menu).getByTestId("model-account-shut");
    expect(within(shut).getByTestId("model-account-state").textContent).toBe("Turned off");
    expect((shut as HTMLButtonElement).disabled).toBe(true);
    expect(shut.getAttribute("title")).toBe("Turned off. Turn it on in Connections to use it.");
    const work = within(menu).getByTestId("model-account-work");
    expect(work.getAttribute("data-current")).toBe("true");
    expect(within(work).getByTestId("model-account-state").textContent).toBe("Removed from Iron-Proxy");
    // The session's own account still opens its models (a model change only).
    expect((work as HTMLButtonElement).disabled).toBe(false);
  });

  it("an unpickable account's models cannot be reached: no panel opens, nothing is sent", async () => {
    renderSession();
    const menu = await openMenu();
    fireEvent.click(within(menu).getByTestId("model-account-side"));
    expect(screen.queryByTestId("model-sub")).toBeNull();
    fireEvent.change(screen.getByTestId("model-filter"), { target: { value: "opus" } });
    const sideRow = (await screen.findAllByTestId("model-match")).find((r) => r.getAttribute("data-account") === "side");
    expect((sideRow as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(screen.getByTestId("model-filter"), { key: "Enter" }); // the first row: Work's
    expect(patches().some((c) => (c.body as { account?: string }).account === "side")).toBe(false);
  });
});

/* ------------------------------------------------------------ the switch */

describe("picking a model under an account", () => {
  it("sends the account AND the model in one request, and the view shows both", async () => {
    api.answers["PATCH /build/sessions/s1"] = () => {
      const v = onWork({
        setup: { provider: "claude-cli", model: "claude-sonnet-4-6", account: "personal", account_name: "Personal" },
        account: PERSONAL,
      });
      api.gets["/build/sessions/s1"] = v;
      return v;
    };
    renderSession();
    const menu = await openMenu();
    expect(screen.getByTestId("account-chip-name")).toHaveTextContent("Work");
    fireEvent.click(within(menu).getByTestId("model-account-personal"));
    const sub = await screen.findByTestId("model-sub");
    fireEvent.click(within(sub).getByText("Sonnet 4.6"));
    await waitFor(() =>
      expect(patches()).toEqual([
        { method: "PATCH", path: "/build/sessions/s1", body: { account: "personal", model: "claude-sonnet-4-6" } },
      ]),
    );
    // Both chips show the result.
    await waitFor(() => expect(screen.getByTestId("account-chip-name")).toHaveTextContent("Personal"));
    expect(screen.getByTestId("model-chip-words")).toHaveTextContent("Sonnet 4.6");
    expect(screen.getByTestId("model-chip-account").textContent).toBe(" · Personal");
    // Never a continue: that is the limit card's.
    expect(api.calls.some((c) => c.path === "/build/sessions/s1/account")).toBe(false);
  });

  it("a Codex account's model moves the session to Codex in the same one request", async () => {
    api.answers["PATCH /build/sessions/s1"] = () =>
      onWork({
        setup: { provider: "codex-cli", model: "gpt-5.5", account: "cx", account_name: "Codex Plus" },
        account: CX,
        account_vendor: "codex",
      });
    renderSession();
    const menu = await openMenu();
    fireEvent.click(within(menu).getByTestId("model-account-cx"));
    fireEvent.click(within(await screen.findByTestId("model-sub")).getByText("gpt-5.5"));
    await waitFor(() =>
      expect(patches()[0]).toEqual({ method: "PATCH", path: "/build/sessions/s1", body: { account: "cx", model: "gpt-5.5" } }),
    );
    expect(patches()).toHaveLength(1);
  });

  it("under the session's OWN account a pick is the model alone, as before", async () => {
    api.answers["PATCH /build/sessions/s1"] = () =>
      onWork({ setup: { provider: "claude-cli", model: "claude-sonnet-4-6", account: "work", account_name: "Work" } });
    renderSession();
    const menu = await openMenu();
    fireEvent.click(within(menu).getByTestId("model-account-work"));
    const sub = await screen.findByTestId("model-sub");
    // The session's model is marked under its own account only.
    expect(within(sub).getByText("Opus 4.8").closest("button")?.className).toContain("text-accent-soft");
    fireEvent.click(within(sub).getByText("Sonnet 4.6"));
    await waitFor(() =>
      expect(patches()).toEqual([
        { method: "PATCH", path: "/build/sessions/s1", body: { provider: "claude-cli", model: "claude-sonnet-4-6" } },
      ]),
    );
  });

  it("a refused account change leaves the account and the model as they were and says the daemon's sentence", async () => {
    const REFUSAL = "That account is turned off on the Iron-Proxy card — turn it on in Connections first.";
    api.answers["PATCH /build/sessions/s1"] = new api.ApiError(REFUSAL, 409);
    renderSession();
    const menu = await openMenu();
    expect(screen.getByTestId("model-chip-words")).toHaveTextContent("Opus 4.8");
    fireEvent.click(within(menu).getByTestId("model-account-capped"));
    fireEvent.click(within(await screen.findByTestId("model-sub")).getByText("Sonnet 4.6"));
    expect(await screen.findByText(REFUSAL)).toBeTruthy();
    // One request, refused whole: no second request moved the model alone.
    expect(patches()).toEqual([
      { method: "PATCH", path: "/build/sessions/s1", body: { account: "capped", model: "claude-sonnet-4-6" } },
    ]);
    expect(screen.getByTestId("model-chip-words")).toHaveTextContent("Opus 4.8");
    expect(screen.getByTestId("model-chip-account").textContent).toBe(" · Work");
    expect(screen.getByTestId("account-chip-name")).toHaveTextContent("Work");
  });

  it("the header chip's switch is the same action: the model chip names the new account", async () => {
    api.answers["PATCH /build/sessions/s1"] = () => {
      const v = onWork({
        setup: { provider: "claude-cli", model: "claude-opus-4-8", account: "personal", account_name: "Personal" },
        account: PERSONAL,
      });
      api.gets["/build/sessions/s1"] = v;
      return v;
    };
    renderSession();
    fireEvent.click(await screen.findByTestId("account-chip"));
    fireEvent.click(await screen.findByTestId("account-pick-personal"));
    await waitFor(() =>
      expect(patches()).toEqual([{ method: "PATCH", path: "/build/sessions/s1", body: { account: "personal" } }]),
    );
    await waitFor(() => expect(screen.getByTestId("model-chip-account").textContent).toBe(" · Personal"));
    fireEvent.click(screen.getByTitle("Switch model"));
    const menu = await screen.findByTestId("model-menu");
    expect(within(menu).getByTestId("model-account-personal").getAttribute("data-current")).toBe("true");
    expect(within(menu).getByTestId("model-account-work").getAttribute("data-current")).toBeNull();
  });
});

/* --------------------------------------------------- no account list */

describe("no account list: Claude Code is the one row it always was", () => {
  for (const [why, answer] of [
    ["Iron-Proxy is off", { claude: [], codex: [], iron_proxy: PROXY_OFF }],
    ["an older Iron Jarvis (404)", new api.ApiError("no such route", 404)],
    ["the list is not read yet", () => new Promise(() => {})],
  ] as const) {
    it(why, async () => {
      api.gets["/build/accounts"] = answer;
      api.gets["/build/sessions/s1"] = view({ setup: { provider: "claude-cli", model: "claude-opus-4-8" } });
      api.answers["PATCH /build/sessions/s1"] = () => view({ setup: { provider: "claude-cli", model: "claude-sonnet-4-6" } });
      renderSession();
      const menu = await openMenu("");
      const claude = await waitFor(() => group(menu, "claude"));
      const way = within(claude).getByTestId("model-way-claude-cli");
      expect(way.tagName).toBe("BUTTON");
      expect(way).toHaveTextContent("Claude Codecloud · on your plan");
      expect(menu.querySelector('[data-testid^="model-account-"]')).toBeNull();
      expect(screen.queryByTestId("model-chip-account")).toBeNull();
      fireEvent.click(way);
      fireEvent.click(within(await screen.findByTestId("model-sub")).getByText("Sonnet 4.6"));
      await waitFor(() =>
        expect(patches()).toEqual([
          { method: "PATCH", path: "/build/sessions/s1", body: { provider: "claude-cli", model: "claude-sonnet-4-6" } },
        ]),
      );
    });
  }
});

/* ------------------------------------------------------------ the chip */

describe("the model chip names the account", () => {
  it('reads "Opus 4.8 · Work"', async () => {
    renderSession();
    await screen.findByTestId("account-chip");
    const chip = screen.getByTitle("Switch model");
    await waitFor(() => expect(chip.textContent).toBe("Opus 4.8 · Work"));
  });

  it("a session left on the vendor's own default names that, never the app's default model", async () => {
    api.gets["/build/sessions/s1"] = onWork({ setup: { provider: "claude-cli", account: "work", account_name: "Work" } });
    renderSession();
    await screen.findByTestId("account-chip");
    await waitFor(() => expect(screen.getByTitle("Switch model").textContent).toBe("Your Claude plan · Work"));
    expect(screen.getByTitle("Switch model").textContent).not.toContain("Demo");
  });

  it("a provider no words table knows, left on its own default, reads by its label", async () => {
    api.models = [...CLAUDE, { provider: "fleet-abc", model: "qwen3", name: "DGX Spark", available: true, kind: "local", on_this_network: true }];
    api.gets["/build/accounts"] = { claude: [], codex: [], iron_proxy: PROXY_OFF };
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "fleet-abc" } });
    renderSession();
    await waitFor(() => expect(screen.getByTitle("Switch model").textContent).toBe("DGX Spark"));
  });

  it("fits the box at 390 px: the chip keeps its width, the account is cut on its own, Send stays in the wrapping row", async () => {
    api.gets["/build/sessions/s1"] = onWork({
      setup: { provider: "claude-cli", model: "claude-opus-4-8", account: "work", account_name: "Work" },
      account: { ...WORK, name: "The very long name of a work account for one client" },
    });
    renderSession();
    const part = await screen.findByTestId("model-chip-account");
    const chip = screen.getByTitle("Switch model");
    // jsdom cannot measure: the rules that make it fit are pinned (a real
    // browser measures it in e2e/ij-harness.spec.ts at 390 px).
    expect(chip.className.split(/\s+/)).toEqual(expect.arrayContaining(["max-w-[11rem]", "sm:max-w-[16rem]"]));
    expect(part.className.split(/\s+/)).toEqual(expect.arrayContaining(["max-w-[6rem]", "truncate", "shrink-0", "min-w-0"]));
    expect(screen.getByTestId("model-chip-words").className.split(/\s+/)).toEqual(
      expect.arrayContaining(["min-w-0", "truncate"]),
    );
    const row = screen.getByTestId("harness-box-actions");
    expect(row.contains(chip)).toBe(true);
    expect(row.contains(screen.getByTestId("harness-send"))).toBe(true);
    expect(row.className.split(/\s+/)).toEqual(expect.arrayContaining(["flex-wrap", "max-w-full", "min-w-0"]));
    // On a phone the menu is a sheet along the window's bottom that scrolls
    // (with the accounts it is taller than the room above the chip: at
    // 390 px it ran past the window's top), and an account's models open
    // UNDER it, inside the sheet (beside it, there is no room on the left).
    fireEvent.click(chip);
    const menu = await screen.findByTestId("model-menu");
    expect(menu.className.split(/\s+/)).toEqual(
      expect.arrayContaining(["max-sm:fixed", "max-sm:inset-x-2", "max-sm:bottom-2", "max-sm:max-h-[70dvh]", "max-sm:overflow-y-auto"]),
    );
    fireEvent.click(await screen.findByTestId("model-account-personal"));
    const panel = await screen.findByTestId("model-sub");
    expect(panel.className.split(/\s+/)).toEqual(
      expect.arrayContaining(["max-sm:static", "max-sm:mr-0", "max-sm:w-full", "absolute", "right-full"]),
    );
  });
});

/* -------------------------------------------------- filter and Recent */

describe("the typed filter and Recent name the account", () => {
  it("a word lists each model once per account, named; the account's name finds its models", async () => {
    renderSession();
    await openMenu();
    const box = screen.getByTestId("model-filter");
    fireEvent.change(box, { target: { value: "sonnet" } });
    const rows = await screen.findAllByTestId("model-match");
    expect(rows.map((r) => r.getAttribute("data-account"))).toEqual(["work", "personal", "near", "capped", "side"]);
    expect(rows[1]).toHaveTextContent("Sonnet 4.6");
    expect(rows[1]).toHaveTextContent("Claude Code · Personal");
    fireEvent.change(box, { target: { value: "personal" } });
    const mine = await screen.findAllByTestId("model-match");
    expect(mine.length).toBeGreaterThan(0);
    expect(mine.every((r) => r.getAttribute("data-account") === "personal")).toBe(true);
    // Enter picks the first: the account and the model together.
    api.answers["PATCH /build/sessions/s1"] = () => onWork();
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(patches()).toHaveLength(1));
    // The first row is Personal's plan default (the CLI's own "subscription").
    expect(patches()[0].body).toEqual({ account: "personal", model: "subscription" });
  });

  it("Recent names the account its pick runs on, and a keyless Anthropic pick reads as Claude Code", async () => {
    localStorage.setItem(
      RECENT_MODELS_KEY,
      JSON.stringify(["anthropic::claude-sonnet-4-6", "codex-cli::gpt-5.5", "ollama::qwen3:8b"]),
    );
    renderSession();
    const menu = await openMenu();
    const recent = within(menu).getByTestId("model-recent");
    const rows = within(recent).getAllByTestId("model-recent-row");
    expect(rows.map((r) => r.textContent)).toEqual([
      "Sonnet 4.6Claude Code · Work",
      "gpt-5.5Codex · Codex Plus",
      "qwen3:8bOllama",
    ]);
    // On the session's own account: the model alone.
    api.answers["PATCH /build/sessions/s1"] = () => onWork();
    fireEvent.click(rows[0]);
    await waitFor(() =>
      expect(patches()[0]).toEqual({
        method: "PATCH",
        path: "/build/sessions/s1",
        body: { provider: "claude-cli", model: "claude-sonnet-4-6" },
      }),
    );
  });
});
