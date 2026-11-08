/**
 * v1.449.0 — readiness Wave C, the chat home (items 26 and 27).
 *
 *  ITEM 26 The home's machine line counts REQUIRED helpers only and folds
 *          the optional misses into one phrase — a PC without Microsoft Word
 *          never reads "5 of 6 helpers ready" on its first screen — and once
 *          a real model answers while the checklist is still open, ONE quiet
 *          line says what to try next, in the step's own words.
 *  ITEM 27 On a phone, New chat is one tap in the chat's top bar (it sat
 *          two taps deep, behind "Chats" in the drawer).
 *
 * The chat page is rendered for real (the first-run-home-v13530 harness).
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
  return {
    FakeApiError,
    getResponses: {} as Record<string, unknown>,
    gets: [] as string[],
    posts: [] as Array<{ path: string; body: unknown }>,
    health: null as Record<string, unknown> | null,
    refresh: vi.fn(),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    return {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    epoch: 0,
    provided: true,
    health: H.health,
    refresh: H.refresh,
  }),
}));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    StreamError,
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async () => ({ reply: "" }),
      abort: () => {},
    }),
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: null,
    engine: null,
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {},
    stop: () => {},
    reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false,
    enabled: false,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {},
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => {
    const h = H.health as { default_provider?: string; providers?: Array<Record<string, unknown>> } | null;
    const byProvider: Record<string, boolean> = {};
    for (const p of h?.providers ?? []) byProvider[String(p.provider)] = Boolean(p.available);
    return {
      byProvider,
      signedOutByProvider: {},
      defaultProvider: h?.default_provider ?? "",
      loading: false,
      stale: false,
      refresh: () => {},
    };
  },
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import {
  NEXT_STEP_WORDS,
  decodeHomeReadiness,
  machineText,
  nextStepText,
  showMachineLine,
} from "@/components/chat/HomeReadiness";
import { claimChatSlot, releaseChatSlot } from "@/lib/sidebarSlot";

type Row = Record<string, unknown>;

const NOTHING_CONNECTED: Row[] = [
  { provider: "anthropic", available: false, class: "api", inherited_from: null },
  { provider: "claude-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "codex-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "ollama", available: false, class: "local", inherited_from: null, detected: false },
];
const CLAUDE_SIGNED_IN: Row[] = NOTHING_CONNECTED.map((r) =>
  r.provider === "claude-cli"
    ? { ...r, available: true, installed: true, signed_in: true }
    : r.provider === "anthropic"
      ? { ...r, available: true, inherited_from: "claude-cli" }
      : r,
);

/** A normal PC without Microsoft Word: every end-user row optional, one off. */
const CHECKS = [
  { name: "browser", ok: true, level: "recommended", label: "A web browser for browsing tasks" },
  { name: "pdf scan routing", ok: true, level: "recommended", label: "Spotting scanned pages inside PDFs" },
  { name: "local_ocr", ok: true, level: "recommended", label: "Reading scanned pages on this PC" },
  { name: "old Office files", ok: false, level: "recommended", label: "Reading old .doc and .xls files" },
  { name: "guide_docs", ok: true, level: "recommended", label: "The built-in help guides" },
  { name: "browser_addon", ok: false, level: "recommended", label: "The browser add-on" },
  { name: "node", ok: false, level: "recommended", label: "Node" },
];

function setHealth(providers: Row[], defaultProvider: string) {
  H.health = { status: "ok", version: "1.449.0", default_provider: defaultProvider, default_model: "x", providers };
}

function setOnboarding(over: Record<string, unknown> = {}) {
  H.getResponses["/onboarding"] = {
    version: "1.449.0",
    first_run: true,
    doctor: { ok: true, checks: CHECKS },
    checklist: [
      { key: "connect_ai", title: "Connect your AI", done: false, optional: false },
      { key: "first_session", title: "Give it your first task", done: false, optional: false },
      { key: "work_with_document", title: "Work with a document", done: false, optional: false },
      { key: "connect_apps", title: "Connect the apps you use (optional)", done: false, optional: true },
    ],
    next_step: { key: "connect_ai", title: "Connect your AI" },
    model: { default_provider: "mock", default_model: "", is_mock: true, usable: [] },
    ...over,
  };
}

function resetApi() {
  for (const k of Object.keys(H.getResponses)) delete H.getResponses[k];
  H.getResponses["/models"] = { models: [] };
  H.getResponses["/chat/personas"] = { personas: [] };
  H.getResponses["/chat/threads"] = { threads: [] };
  H.getResponses["/settings"] = { settings: {} };
  H.getResponses["/projects"] = { projects: [] };
  H.getResponses["/agents/mentionable"] = { agents: [] };
  H.getResponses["/skills"] = { skills: [] };
  H.getResponses["/workflows"] = { workflows: [] };
  H.getResponses["/tools"] = { tools: [] };
  H.getResponses["/undo?session_id=chat"] = { actions: [] };
}

const FIRST_CHIP = "What can you do?";

beforeEach(() => {
  resetApi();
  H.gets = [];
  H.posts = [];
  H.refresh = vi.fn();
  setHealth(NOTHING_CONNECTED, "mock");
  setOnboarding();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ================================================================ item 26 === */

describe("item 26 — the home counts REQUIRED helpers and folds the optional ones", () => {
  it("decoding: a normal PC without Word reads ready, with its extras folded, never '4 of 6'", () => {
    const h = decodeHomeReadiness({ doctor: { ok: true, checks: CHECKS }, checklist: [], next_step: null })!;
    expect([h.ready, h.total]).toEqual([4, 6]); // the old count, still decoded
    expect([h.requiredReady, h.requiredTotal]).toEqual([0, 0]);
    expect(h.optionalMissing).toEqual(["Reading old .doc and .xls files", "The browser add-on"]);
    expect(h.requiredMissing).toBe(false);
    expect(machineText(h)).toBe("This PC is ready · 2 optional extras not set up");
    // One optional miss, one required row ready.
    const one = decodeHomeReadiness({
      doctor: {
        ok: true,
        checks: [
          { name: "guide_docs", ok: true, level: "required", label: "The built-in help guides" },
          { name: "old Office files", ok: false, level: "recommended", label: "Reading old .doc and .xls files" },
        ],
      },
    })!;
    expect(machineText(one)).toBe("This PC: 1 of 1 helper ready · 1 optional extra not set up");
    // A REQUIRED miss is still named first, and shows even on a settled PC.
    const required = decodeHomeReadiness({
      doctor: {
        ok: false,
        checks: [
          { name: "guide_docs", ok: false, level: "required", label: "The built-in help guides" },
          { name: "old Office files", ok: false, level: "recommended", label: "Reading old .doc and .xls files" },
        ],
      },
    })!;
    expect(machineText(required)).toBe(
      "This PC: 0 of 1 helper ready — The built-in help guides · 1 optional extra not set up",
    );
    expect(showMachineLine(required)).toBe(true);
    // Everything ready: the plain word.
    const all = decodeHomeReadiness({ doctor: { ok: true, checks: CHECKS.map((c) => ({ ...c, ok: true })) } })!;
    expect(machineText(all)).toBe("This PC is ready");
  });

  it("on the home: 'This PC is ready · 2 optional extras not set up · Details' for a new user", async () => {
    render(<ChatPage />);
    const line = await screen.findByTestId("home-machine");
    expect(line.textContent).toBe("This PC is ready · 2 optional extras not set up · Details");
    expect(line.textContent).not.toMatch(/of 6 helpers/);
    expect(within(line).getByRole("link", { name: "Details" }).getAttribute("href")).toBe("/everything#status");
  });

  it("the next-step words: the open step in its own words; nothing while the demo answers or for the connect step", () => {
    const base = decodeHomeReadiness({
      doctor: { ok: true, checks: [] },
      next_step: { key: "work_with_document", title: "Work with a document" },
      model: { is_mock: false },
    })!;
    expect(nextStepText(base)).toBe(`Next: ${NEXT_STEP_WORDS.work_with_document}`);
    expect(nextStepText({ ...base, nextStep: { key: "first_session", title: "" } })).toBe(
      `Next: ${NEXT_STEP_WORDS.first_session}`,
    );
    expect(nextStepText({ ...base, nextStep: { key: "teach_style", title: "" } })).toMatch(/^Next: Teach it your style/);
    // An unknown step reads its title; no step, the demo, or the connect step say nothing.
    expect(nextStepText({ ...base, nextStep: { key: "later_step", title: "Set up a backup" } })).toBe(
      "Next: Set up a backup",
    );
    expect(nextStepText({ ...base, nextStep: null })).toBe("");
    expect(nextStepText({ ...base, isMock: true })).toBe("");
    expect(nextStepText({ ...base, nextStep: { key: "connect_ai", title: "Connect your AI" } })).toBe("");
    expect(nextStepText(null)).toBe("");
  });

  it("on the home: once a real model answers and a step is open, ONE line says what to try next", async () => {
    setHealth(CLAUDE_SIGNED_IN, "anthropic");
    setOnboarding({
      first_run: false,
      next_step: { key: "work_with_document", title: "Work with a document" },
      model: { default_provider: "anthropic", default_model: "x", is_mock: false, usable: [] },
    });
    render(<ChatPage />);
    const next = await screen.findByTestId("home-next-step");
    expect(next.textContent).toBe(
      "Next: Work with a document — drop a PDF or spreadsheet into the box and ask for a summary. All steps",
    );
    expect(within(next).getByRole("link", { name: "All steps" }).getAttribute("href")).toBe("/everything#status");
    expect(screen.getAllByTestId("home-next-step")).toHaveLength(1);
  });

  it("CONTROL: no next-step line while the demo answers, nor once the checklist is done", async () => {
    render(<ChatPage />);
    await screen.findByTestId("home-machine");
    expect(screen.queryByTestId("home-next-step")).toBeNull();
    cleanup();
    setHealth(CLAUDE_SIGNED_IN, "anthropic");
    setOnboarding({
      first_run: false,
      next_step: null,
      model: { default_provider: "anthropic", default_model: "x", is_mock: false, usable: [] },
    });
    render(<ChatPage />);
    await screen.findByRole("button", { name: FIRST_CHIP });
    await waitFor(() => expect(H.gets).toContain("/onboarding"));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("home-next-step")).toBeNull();
  });
});

/* ================================================================ item 27 === */

describe("item 27 — on a phone, New chat is one tap in the top bar", () => {
  it("the top bar carries New chat below md, beside Chats, and it starts a new conversation", async () => {
    H.getResponses["/chat/threads"] = {
      threads: [{ id: "t1", title: "Thank-you email", updated_at: "2026-11-07T10:00:00", message_count: 2 }],
    };
    H.getResponses["/chat/threads/t1"] = {
      id: "t1",
      title: "Thank-you email",
      messages: [
        { role: "user", content: "Draft a thank-you email." },
        { role: "assistant", content: "The thank-you draft." },
      ],
    };
    window.history.replaceState({}, "", "/chat?thread=t1");
    render(<ChatPage />);
    await screen.findByText("The thank-you draft.");
    const door = screen.getByTestId("chat-new-chat-phone");
    expect(door.getAttribute("aria-label")).toBe("New chat");
    expect((door.getAttribute("class") ?? "").split(/\s+/)).toContain("md:hidden");
    expect(door.closest('[data-testid="chat-topbar"]')).not.toBeNull();
    // It does what the rail's New chat does: the conversation is left.
    fireEvent.click(door);
    await waitFor(() => expect(screen.queryByText("The thank-you draft.")).toBeNull());
    await screen.findByTestId("chat-hero-foot");
  });

  it("not drawn while a sidebar holds the chat list (its New chat is right there)", async () => {
    const slot = document.createElement("div");
    slot.id = "ij-drawer-chat-slot";
    document.body.appendChild(slot);
    claimChatSlot(slot, () => {});
    try {
      render(<ChatPage />);
      await screen.findByRole("button", { name: FIRST_CHIP });
      expect(screen.queryByTestId("chat-new-chat-phone")).toBeNull();
      expect(screen.queryByTestId("chat-open-chats")).toBeNull();
    } finally {
      releaseChatSlot(slot);
      slot.remove();
    }
  });
});
