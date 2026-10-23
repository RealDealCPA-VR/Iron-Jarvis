/**
 * v1.376.0 — the IJ harness's model menu groups providers by COMPANY and lists
 * the Iron-Proxy accounts (components/chat/ModelMenu `companies`). The CHAT
 * page draws the SAME ModelMenu, and the user asked for nothing to change
 * there: this file renders the real chat page (only the transport mocked, the
 * model-menu-v1277 harness) and compares the menu's markup — the chip, the
 * provider tree, every provider's model panel, the typed filter, Recent, and
 * the chip after a pick — with what the chat page drew BEFORE the change
 * (fixtures/chat-model-menu-v1376.json, written from the unchanged code;
 * IJ_REGEN_CHAT_MODEL_MENU=1 writes it again after an INTENDED change).
 *
 * The catalog holds exactly what the harness now folds: Claude Code beside a
 * keyless Anthropic it serves (inherited_from), Codex beside an OpenAI key,
 * a local model, a cloud endpoint and an offline provider. The chat must keep
 * listing every one of them as its own provider row.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return {};
  },
  put: async (path: string) => {
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
    run: async () => ({ reply: "done" }),
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
import { RECENT_MODELS_KEY } from "@/lib/recentModels";

/** Claude Code AND a keyless Anthropic it serves; Codex AND an OpenAI key. */
export const CHAT_CATALOG = {
  models: [
    { provider: "claude-cli", model: "subscription", available: true, kind: "cli", inherited_from: null },
    {
      provider: "claude-cli", model: "claude-opus-4-8", label: "Opus 4.8", available: true, kind: "cli",
      inherited_from: null, context_window: 1_000_000, reasoning: ["low", "medium", "high"],
    },
    { provider: "claude-cli", model: "claude-sonnet-4-6", label: "Sonnet 4.6", available: true, kind: "cli", inherited_from: null },
    { provider: "anthropic", model: "claude-opus-4-8", label: "Opus 4.8", available: true, kind: "api", inherited_from: "claude-cli" },
    { provider: "anthropic", model: "claude-sonnet-4-6", label: "Sonnet 4.6", available: true, kind: "api", inherited_from: "claude-cli" },
    { provider: "anthropic", model: "claude-haiku-4-5", available: true, kind: "api", inherited_from: "claude-cli" },
    { provider: "codex-cli", model: "subscription", available: true, kind: "cli", inherited_from: null },
    { provider: "codex-cli", model: "gpt-5.5", available: true, kind: "cli", inherited_from: null },
    { provider: "openai", model: "gpt-5.5", available: true, kind: "api", inherited_from: null },
    { provider: "ollama", model: "qwen3:8b", available: true, kind: "local", on_this_network: true, size_b: 8 },
    { provider: "fleet-x", model: "frontier", name: "Cloud box", available: true, kind: "local", on_this_network: false },
    { provider: "xai", model: "grok-4", available: false, kind: "api" },
  ],
};

const FIXTURE = join(__dirname, "fixtures", "chat-model-menu-v1376.json");

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.getResponses = {
    "/models": CHAT_CATALOG,
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
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function chip(): Promise<HTMLElement> {
  await screen.findByPlaceholderText(/Message Iron Jarvis/);
  return screen.findByTitle("Switch model");
}

/** Every look of the chat page's model menu, as markup. */
async function drawEverything(): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  const c = await chip();
  out.chip = c.outerHTML;
  fireEvent.click(c);
  const menu = await screen.findByTestId("model-menu");
  // Wait for the catalog's rows (the tree is drawn from GET /models).
  await within(menu).findByText("Claude Code");
  out.tree = menu.outerHTML;
  const rows = within(menu)
    .getAllByRole("button")
    .filter((b) => b.getAttribute("aria-expanded") !== null);
  out.providerRows = String(rows.length);
  for (const row of rows) {
    if ((row as HTMLButtonElement).disabled) continue;
    fireEvent.click(row);
    const sub = await screen.findByTestId("model-sub");
    out[`sub:${row.textContent}`] = sub.outerHTML;
    fireEvent.click(row); // closes it again
  }
  fireEvent.change(screen.getByTestId("model-filter"), { target: { value: "opus" } });
  await screen.findAllByTestId("model-match");
  out.filter = screen.getByTestId("model-menu").outerHTML;
  fireEvent.change(screen.getByTestId("model-filter"), { target: { value: "gpt" } });
  await screen.findAllByTestId("model-match");
  out.filterGpt = screen.getByTestId("model-menu").outerHTML;
  fireEvent.change(screen.getByTestId("model-filter"), { target: { value: "" } });
  // A pick from the tree: Claude Code's Opus 4.8.
  const claude = within(screen.getByTestId("model-menu"))
    .getAllByRole("button")
    .find((b) => b.getAttribute("aria-expanded") !== null && b.textContent?.startsWith("Claude Code"));
  fireEvent.click(claude as HTMLElement);
  fireEvent.click(within(await screen.findByTestId("model-sub")).getByText("Opus 4.8"));
  const after = await chip();
  out.chipAfterPick = after.outerHTML;
  fireEvent.click(after);
  const again = await screen.findByTestId("model-menu");
  out.treeAfterPick = again.outerHTML;
  // Review: the picked model's own panel — its row marked with the check —
  // and the keys: Escape in the box closes the menu and moves no focus
  // (the harness's menu hands focus back to its chip; the chat's never did).
  const claudeAgain = within(again)
    .getAllByRole("button")
    .find((b) => b.getAttribute("aria-expanded") !== null && b.textContent?.startsWith("Claude Code"));
  fireEvent.click(claudeAgain as HTMLElement);
  out.subAfterPick = (await screen.findByTestId("model-sub")).outerHTML;
  fireEvent.keyDown(screen.getByTestId("model-filter"), { key: "Escape" });
  out.escapeCloses = String(screen.queryByTestId("model-menu") === null);
  out.focusAfterEscape = document.activeElement?.tagName ?? "";
  return out;
}

describe("the chat page's model menu is exactly as before (v1.376.0)", () => {
  it("draws the same chip, tree, panels, filter and Recent — byte for byte", async () => {
    window.localStorage.setItem(
      RECENT_MODELS_KEY,
      JSON.stringify(["anthropic::claude-sonnet-4-6", "codex-cli::gpt-5.5", "openai::gpt-5.5"]),
    );
    render(<ChatPage />);
    const now = await drawEverything();
    if (process.env.IJ_REGEN_CHAT_MODEL_MENU === "1") {
      writeFileSync(FIXTURE, `${JSON.stringify(now, null, 2)}\n`, "utf8");
    }
    // Read with LF (the CRLF rule: a CI checkout may carry \r\n).
    const before = JSON.parse(readFileSync(FIXTURE, "utf8").replace(/\r\n/g, "\n")) as Record<string, string>;
    expect(Object.keys(now).sort()).toEqual(Object.keys(before).sort());
    for (const key of Object.keys(before)) expect(now[key], key).toBe(before[key]);
  });

  it("keeps Anthropic and Claude Code as two rows, and OpenAI beside Codex, with no account rows", async () => {
    render(<ChatPage />);
    fireEvent.click(await chip());
    const menu = await screen.findByTestId("model-menu");
    await within(menu).findByText("Claude Code");
    expect(within(menu).getByText("Anthropic")).toBeTruthy();
    expect(within(menu).getByText("Codex")).toBeTruthy();
    expect(within(menu).getByText("OpenAI")).toBeTruthy();
    expect(within(menu).queryByTestId("model-company")).toBeNull();
    expect(within(menu).queryByTestId(/^model-account-/)).toBeNull();
  });
});
