/**
 * v1.424.0 (wave 4, D6) — an ACCOUNT can be chosen in a plain chat, and Grok
 * accounts are a coding session's choices too.
 *
 * Before: only a Build coding session could be put on one Iron-Proxy account;
 * every chat turn leased Iron-Proxy's first free account, and Grok had no
 * account entries anywhere. Now:
 *
 * * the CHAT page's model menu lists, under Claude Code / Codex / Grok, the
 *   same account entries the coding session's menu has (lib/harnessModelMenu
 *   `cliAccounts`), plus "Any free account";
 * * a pick under an account rides every turn as `account` (only for the
 *   provider it serves), is named on the chip and kept in the chat's setup;
 * * Iron-Proxy off / an older Iron Jarvis (a 404): no entries — the menu is
 *   exactly as before (chat-model-menu-unchanged-v1376 stays byte for byte);
 * * the coding session's menu groups Grok (xAI) with its accounts.
 *
 * The page is the real chat page with only the transport mocked (the
 * model-menu-v1277 harness).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
    stream: { bodies: [] as Record<string, unknown>[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async () => ({}),
  patch: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("done", "done");
      return { reply: "done" };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { decodeAccounts, type AccountsView } from "@/lib/buildAccounts";
import { accountAfterPick, accountForTurn, chatAccountsFor } from "@/lib/chatAccount";
import { companyMenu } from "@/lib/harnessModelMenu";
import type { ModelOption } from "@/lib/types";

const CATALOG = {
  models: [
    { provider: "claude-cli", model: "subscription", available: true, kind: "cli", inherited_from: null },
    { provider: "claude-cli", model: "claude-opus-4-8", label: "Opus 4.8", available: true, kind: "cli", inherited_from: null },
    { provider: "codex-cli", model: "subscription", available: true, kind: "cli", inherited_from: null },
    { provider: "codex-cli", model: "gpt-6-sol", label: "GPT-6-Sol", available: true, kind: "cli", inherited_from: null },
    { provider: "grok-cli", model: "grok-build-0.1", available: true, kind: "cli", inherited_from: null },
    { provider: "xai", model: "grok-4", available: false, kind: "api" },
    { provider: "ollama", model: "qwen3:8b", available: true, kind: "local", on_this_network: true },
  ],
};

function row(id: string, vendor: string, name: string, extra: Record<string, unknown> = {}) {
  const label = { claude: "Claude Code", codex: "Codex", grok: "Grok" }[vendor];
  return { id, name, vendor, vendor_label: label, status: "ready", default: false, ...extra };
}

/** GET /build/accounts as the daemon answers it (v1.424.0: with `grok`). */
const ACCOUNTS = {
  claude: [row("c-work", "claude", "Work Max", { default: true, plan_label: "Max" }), row("c-spare", "claude", "Spare")],
  codex: [row("x-plus", "codex", "Codex Plus", { default: true })],
  grok: [row("g-work", "grok", "Grok Work", { default: true })],
  iron_proxy: { enabled: true, running: true, needs_update: false, note: "" },
};

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.api.getResponses = {
    "/models": CATALOG,
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
    "/build/accounts": ACCOUNTS,
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function openMenu(): Promise<HTMLElement> {
  await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.click(await screen.findByTitle("Switch model"));
  const menu = await screen.findByTestId("model-menu");
  return menu;
}

async function send(text: string) {
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(H.stream.bodies.length).toBeGreaterThan(0));
  return H.stream.bodies[H.stream.bodies.length - 1];
}

const view = (): AccountsView => decodeAccounts(ACCOUNTS) as AccountsView;

describe("the rules (lib/chatAccount)", () => {
  it("draws nothing with no list and no account — the menu as before", () => {
    expect(chatAccountsFor(null, null)).toBeUndefined();
    const off = decodeAccounts({ claude: [], codex: [], grok: [], iron_proxy: { enabled: false } });
    expect(chatAccountsFor(off, null)).toBeUndefined();
  });

  it("lists each CLI's accounts, Grok's too, and the chat's own marked", () => {
    const got = chatAccountsFor(view(), { id: "c-spare", name: "Spare", provider: "claude-cli" });
    expect(got?.current).toBe("c-spare");
    expect(got?.byProvider.get("claude-cli")?.map((a) => [a.id, a.current])).toEqual([
      ["c-work", false],
      ["c-spare", true],
    ]);
    expect(got?.byProvider.get("grok-cli")?.map((a) => a.id)).toEqual(["g-work"]);
    expect(got?.byProvider.get("codex-cli")?.map((a) => a.id)).toEqual(["x-plus"]);
  });

  it("a pick keeps, names or drops the account, never on another vendor", () => {
    const spare = { id: "c-spare", name: "Spare", provider: "claude-cli" };
    // Under an account: that account.
    expect(accountAfterPick(null, "claude-cli::claude-opus-4-8", "c-spare", view())).toEqual(spare);
    // "Any free account": none.
    expect(accountAfterPick(spare, "claude-cli::claude-opus-4-8", "", view())).toBeNull();
    // Recent / filter on the same CLI: kept. On another provider: dropped.
    expect(accountAfterPick(spare, "claude-cli::subscription", undefined, view())).toEqual(spare);
    expect(accountAfterPick(spare, "codex-cli::gpt-6-sol", undefined, view())).toBeNull();
    expect(accountAfterPick(spare, "", undefined, view())).toBeNull();
    // A turn sends it only on the provider it serves.
    expect(accountForTurn(spare, "claude-cli::claude-opus-4-8")).toBe("c-spare");
    expect(accountForTurn(spare, "ollama::qwen3:8b")).toBe("");
    expect(accountForTurn(null, "claude-cli::claude-opus-4-8")).toBe("");
  });

  it("the coding session's menu groups Grok (xAI) with its accounts", () => {
    const menu = companyMenu(CATALOG.models as ModelOption[], view(), null, "");
    const grok = menu.entries.find((e) => e.type === "company" && e.id === "grok");
    expect(grok && grok.type === "company" ? grok.label : "").toBe("Grok (xAI)");
    const ways = grok && grok.type === "company" ? grok.ways : [];
    const cli = ways.find((w) => w.provider === "grok-cli");
    expect(cli?.accounts?.map((a) => a.id)).toEqual(["g-work"]);
    expect(ways.find((w) => w.provider === "xai")?.accounts).toBeNull();
  });
});

describe("the chat page's model menu (v1.424.0)", () => {
  it("lists the accounts under each subscription CLI, and Any free account", async () => {
    render(<ChatPage />);
    const menu = await openMenu();
    await within(menu).findByTestId("model-account-c-spare");
    expect(within(menu).getByTestId("model-account-any-claude-cli")).toBeTruthy();
    expect(within(menu).getByTestId("model-account-x-plus")).toBeTruthy();
    expect(within(menu).getByTestId("model-account-g-work")).toBeTruthy();
    // Nothing chosen: "Any free account" is this chat's way.
    expect(within(menu).getByTestId("model-account-any-claude-cli").getAttribute("data-current")).toBe("true");
    // A provider with no accounts keeps its one row (no company groups here).
    expect(within(menu).getByText("Ollama")).toBeTruthy();
    expect(within(menu).queryByTestId("model-company")).toBeNull();
  });

  it("a pick under an account rides the turn, shows on the chip and is saved", async () => {
    render(<ChatPage />);
    const menu = await openMenu();
    fireEvent.click(await within(menu).findByTestId("model-account-c-spare"));
    fireEvent.click(within(await screen.findByTestId("model-sub")).getByText("Opus 4.8"));
    await waitFor(() => expect(screen.queryByTestId("model-menu")).toBeNull());
    expect((await screen.findByTestId("model-chip-account")).textContent).toContain("Spare");
    const body = await send("hello");
    expect(body.provider).toBe("claude-cli");
    expect(body.model).toBe("claude-opus-4-8");
    expect(body.account).toBe("c-spare");
    await waitFor(() => expect(H.api.puts.length).toBeGreaterThan(0));
    const setup = H.api.puts[H.api.puts.length - 1].body.setup as Record<string, unknown>;
    expect(setup.account).toBe("c-spare");
    expect(setup.account_name).toBe("Spare");
  });

  it("Any free account sends no account; a pick on another provider drops it", async () => {
    render(<ChatPage />);
    let menu = await openMenu();
    fireEvent.click(await within(menu).findByTestId("model-account-c-spare"));
    fireEvent.click(within(await screen.findByTestId("model-sub")).getByText("Opus 4.8"));
    await waitFor(() => expect(screen.queryByTestId("model-menu")).toBeNull());
    fireEvent.click(await screen.findByTitle("Switch model"));
    menu = await screen.findByTestId("model-menu");
    fireEvent.click(await within(menu).findByTestId("model-account-any-claude-cli"));
    fireEvent.click(within(await screen.findByTestId("model-sub")).getByText("Opus 4.8"));
    await waitFor(() => expect(screen.queryByTestId("model-chip-account")).toBeNull());
    const body = await send("hello");
    expect(body.provider).toBe("claude-cli");
    expect("account" in body).toBe(false);
  });

  it("an older Iron Jarvis (no account list) keeps the menu as it was, and sends none", async () => {
    delete H.api.getResponses["/build/accounts"];
    render(<ChatPage />);
    const menu = await openMenu();
    await within(menu).findByText("Claude Code");
    expect(within(menu).queryByTestId(/^model-account-/)).toBeNull();
    const body = await send("hello");
    expect("account" in body).toBe(false);
  });

  it("the account list is read when the menu opens, never on page load", async () => {
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    expect(H.api.gets).not.toContain("/build/accounts");
    await openMenu();
    await waitFor(() => expect(H.api.gets).toContain("/build/accounts"));
  });
});
