/**
 * v1.329.0 (calm chat W5 G1): "Jump to latest" never covers a composer menu.
 *
 * The pill floats just above the dock, and the composer's menus open upward
 * into that same space. On a phone it sat on top of the open "@" menu and hid
 * its Chats rows whenever the transcript was not at the bottom (shots
 * ra__at__phone.png, raday__at__phone.png). It now steps aside while ANY
 * composer menu is open: the typed "@" and "/" menus (read from the composer
 * store inside components/chat/JumpToLatest, never by the page) and the page's
 * own "+", tools, model, project and permission menus. Closing the menu brings
 * it back.
 *
 * Harness: the composer-card-v1326 mocks (ChatPage rendered for real).
 */

import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    api: { responses: {} as Record<string, unknown> },
    /** Force lib/composerMenus.slashMenuOpen to answer "closed". */
    forceSlashClosed: false,
  };
});

// The "/" picker must read its open state from the ONE rule the pill reads
// (lib/composerMenus.slashMenuOpen). Overriding that rule must close the
// picker; an inline copy of the rule would ignore the override.
vi.mock("@/lib/composerMenus", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/composerMenus")>();
  return {
    ...real,
    slashMenuOpen: (s: Parameters<typeof real.slashMenuOpen>[0], busy: boolean) =>
      H.forceSlashClosed ? false : real.slashMenuOpen(s, busy),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    const r = H.api.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
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
    run: () => Promise.resolve({ reply: "Noted.", tools_used: [] }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: true, reason: null, engine: null, listening: false, processing: false,
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
  useProviderHealth: () => ({
    byProvider: {}, signedOutByProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...(rest as Record<string, never>)}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/project/ProjectSurfaces", () => ({
  ProjectSurface: ({ view }: { view: string }) => <div data-testid="surface-stub">surface:{view}</div>,
}));

import ChatPage from "@/app/chat/page";
import {
  anyComposerMenuOpen,
  atMenuOpen,
  slashMenuOpen,
  tokenMenuOpen,
  type TokenMenuState,
} from "@/lib/composerMenus";

const AT = "2026-10-01T10:00:00Z";
const CHAT = {
  id: "ta",
  title: "Ledger chat",
  updated_at: AT,
  messages: [
    { role: "user", content: "read the ledger" },
    { role: "assistant", content: "The ledger is read." },
  ],
};

const pill = () => screen.queryByRole("button", { name: /Jump to latest/ });
const box = async () => (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;

/** Open the saved chat and scroll the reader up, so the pill shows. */
async function scrolledUp() {
  window.history.replaceState({}, "", "/chat?thread=ta");
  render(<ChatPage />);
  await screen.findByText("The ledger is read.");
  const scroller = screen.getByTestId("chat-dock").previousElementSibling as HTMLElement;
  Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 2000 });
  Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 500 });
  Object.defineProperty(scroller, "scrollTop", { configurable: true, writable: true, value: 0 });
  fireEvent.scroll(scroller);
  await screen.findByRole("button", { name: /Jump to latest/ });
}

beforeEach(() => {
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/chat/threads/ta": CHAT,
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [{ name: "summarize", description: "Sum it up" }] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  H.forceSlashClosed = false;
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ---------------------------------------------------------------- pure */

const S = (text: string, extra: Partial<TokenMenuState> = {}): TokenMenuState => ({
  text,
  caret: text.length,
  slashDismissed: false,
  atDismissed: false,
  ...extra,
});

describe("lib/composerMenus", () => {
  it("an @ token at the caret opens the @ menu", () => {
    expect(atMenuOpen(S("ask @"), false)).toBe(true);
    expect(atMenuOpen(S("ask @bui"), false)).toBe(true);
    expect(tokenMenuOpen(S("ask @"), false)).toBe(true);
  });
  it("a / token at the caret opens the / menu, and the @ menu yields to it", () => {
    expect(slashMenuOpen(S("/sum"), false)).toBe(true);
    expect(atMenuOpen(S("/sum"), false)).toBe(false);
    expect(tokenMenuOpen(S("/sum"), false)).toBe(true);
  });
  it("closed: plain words, an email address, a path, a waved-away token, a running turn", () => {
    for (const t of ["hello", "mail me@example.com", "C:/Users", "and/or"]) {
      expect(tokenMenuOpen(S(t), false)).toBe(false);
    }
    expect(atMenuOpen(S("ask @", { atDismissed: true }), false)).toBe(false);
    expect(slashMenuOpen(S("/sum", { slashDismissed: true }), false)).toBe(false);
    expect(tokenMenuOpen(S("ask @"), true)).toBe(false);
    expect(tokenMenuOpen(S("/sum"), true)).toBe(false);
  });
  it("the caret decides: a token behind the caret, not the end of the text", () => {
    expect(atMenuOpen({ ...S("ask @x then more"), caret: 6 }, false)).toBe(true);
    expect(atMenuOpen(S("ask @x then more"), false)).toBe(false);
  });
  it("any of the page's own menus counts", () => {
    const none = { plus: false, tools: false, model: false, project: false, permission: false, promptForm: false };
    expect(anyComposerMenuOpen(none)).toBe(false);
    for (const k of Object.keys(none) as (keyof typeof none)[]) {
      expect(anyComposerMenuOpen({ ...none, [k]: true })).toBe(true);
    }
  });
});

/* ---------------------------------------------------------------- page */

describe("the pill steps aside while a composer menu is open", () => {
  it("the @ menu: hidden while it is open, back when the token is gone", async () => {
    await scrolledUp();
    const b = await box();
    fireEvent.change(b, { target: { value: "ask @" } });
    await screen.findByTestId("at-menu");
    expect(pill()).toBeNull();
    fireEvent.change(b, { target: { value: "ask nobody" } });
    await waitFor(() => expect(screen.queryByTestId("at-menu")).toBeNull());
    await waitFor(() => expect(pill()).not.toBeNull());
  });

  it("the / menu: hidden while it is open, back when it closes", async () => {
    await scrolledUp();
    const b = await box();
    fireEvent.change(b, { target: { value: "/" } });
    await screen.findByRole("listbox", { name: "Skills" });
    expect(pill()).toBeNull();
    fireEvent.change(b, { target: { value: "" } });
    await waitFor(() => expect(pill()).not.toBeNull());
  });

  it("the + menu", async () => {
    await scrolledUp();
    const plus = screen.getByRole("button", { name: "Open the chat menu" });
    fireEvent.click(plus);
    await waitFor(() => expect(plus.getAttribute("aria-expanded")).toBe("true"));
    expect(pill()).toBeNull();
    fireEvent.click(plus);
    await waitFor(() => expect(pill()).not.toBeNull());
  });

  it("the model menu", async () => {
    await scrolledUp();
    const chip = screen.getByTestId("model-chip-words").closest("button") as HTMLButtonElement;
    fireEvent.click(chip);
    await waitFor(() => expect(chip.getAttribute("aria-expanded")).toBe("true"));
    expect(pill()).toBeNull();
    fireEvent.click(chip);
    await waitFor(() => expect(pill()).not.toBeNull());
  });

  it("the permission chip's menu (it lives in a portal, so the chip reports it)", async () => {
    await scrolledUp();
    const chip = document.getElementById("chat-approval-mode") as HTMLButtonElement;
    fireEvent.click(chip);
    await waitFor(() => expect(chip.getAttribute("aria-expanded")).toBe("true"));
    expect(pill()).toBeNull();
    fireEvent.click(chip);
    await waitFor(() => expect(chip.getAttribute("aria-expanded")).toBe("false"));
    await waitFor(() => expect(pill()).not.toBeNull());
  });

  it("the / picker opens by the same rule the pill reads (lib/composerMenus.slashMenuOpen)", async () => {
    H.forceSlashClosed = true;
    render(<ChatPage />);
    const b = await box();
    fireEvent.change(b, { target: { value: "/" } });
    // Give the picker a chance to open (it would, with an inline rule).
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("listbox", { name: "Skills" })).toBeNull();
    // CONTROL: with the shared rule answering "open", the same keystroke opens it.
    H.forceSlashClosed = false;
    fireEvent.change(b, { target: { value: "" } });
    fireEvent.change(b, { target: { value: "/" } });
    await screen.findByRole("listbox", { name: "Skills" });
  });

  it("CONTROL: typing plain words keeps the pill", async () => {
    await scrolledUp();
    const b = await box();
    fireEvent.change(b, { target: { value: "mail me@example.com" } });
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("at-menu")).toBeNull();
    expect(pill()).not.toBeNull();
  });
});
