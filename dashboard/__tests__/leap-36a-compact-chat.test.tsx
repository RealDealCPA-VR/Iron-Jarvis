/**
 * Quick Ask: the answer appears right there (next leaps 36-a, R2.2).
 *
 * The desktop's answer window opens THE CHAT PAGE at
 * `/chat?new=1&quick=1&compact=1&popout=1` (desktop/main.js
 * `quickAnswerUrl`). `?compact=1` (or the desktop saying this window is the
 * answer window, which survives a reload) draws it small: the messages, the
 * composer at the bottom and a slim header with "Continue in the main
 * window"; no chat list, no Chat | Code switch, no project drawer.
 * Everything else is the chat page as it is: the same stream lane (the turn
 * body is the ordinary one), the same save, the same cards.
 *
 * Driven on the REAL page (transport mocked, as the v1506 quick window test
 * does):
 *  - the words are taken once and sent through the ordinary stream body;
 *  - Continue names the SAVED chat's id, and waits until there is one;
 *  - Esc hides the window (even mid-reply: the reply carries on), but never
 *    while a menu or dialog is open, which Esc closes first;
 *  - a link to another page opens in the main window; a chat link stays;
 *  - the hotkey pressed while the window is open puts the cursor in the box;
 *  - the full page is unchanged without `?compact=1`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      release: null as null | ((r: Record<string, unknown>) => void),
      aborts: 0,
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
    mcpAsks: [],
    run: (body: Record<string, unknown>) => {
      H.stream.bodies.push(body);
      return new Promise<Record<string, unknown>>((res) => {
        H.stream.release = res;
      });
    },
    abort: () => {
      H.stream.aborts += 1;
    },
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

import { readFileSync } from "node:fs";
import { join } from "node:path";
import ChatPage from "@/app/chat/page";
import { QUICK_COMPACT_PARAM, isCompactChat, mainWindowPath } from "@/lib/quickAnswer";
import { headingSlug, sectionMatches } from "@/lib/helpLinks";
import { hotkeyLines } from "@/components/help/GlobalHotkeys";

type QuickBridge = {
  take: () => Promise<unknown>;
  isAnswerWindow?: boolean;
  toMain?: (t: unknown) => Promise<unknown>;
  hide?: () => Promise<unknown>;
  onFocus?: (cb: () => void) => () => void;
};
type Bridge = { isDesktop: boolean; quickAsk?: QuickBridge; popout?: Record<string, unknown> };
const W = window as unknown as { ironjarvis?: Bridge };

let take: ReturnType<typeof vi.fn<() => Promise<unknown>>>;
let toMain: ReturnType<typeof vi.fn<(t: unknown) => Promise<unknown>>>;
let hide: ReturnType<typeof vi.fn<() => Promise<unknown>>>;
let focusListeners: (() => void)[];

const COMPACT_URL = `/chat?new=1&quick=1&${QUICK_COMPACT_PARAM}=1&popout=1`;

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.release = null;
  H.stream.aborts = 0;
  H.api.getResponses = {
    "/models": { models: [] },
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
  take = vi.fn(async () => "plain English?");
  toMain = vi.fn(async () => ({ ok: true }));
  hide = vi.fn(async () => true);
  focusListeners = [];
  W.ironjarvis = {
    isDesktop: true,
    quickAsk: {
      take,
      isAnswerWindow: true,
      toMain,
      hide,
      onFocus: (cb) => {
        focusListeners.push(cb);
        return () => {
          focusListeners = focusListeners.filter((f) => f !== cb);
        };
      },
    },
    popout: { isPopout: true, path: "/chat", open: async () => null, list: async () => [], focus: async () => null, close: async () => null },
  };
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  delete W.ironjarvis;
  window.history.replaceState({}, "", "/chat");
});

const box = () => screen.getByTestId("chat-composer").querySelector("textarea") as HTMLTextAreaElement;

async function askedAndSaved() {
  await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
  // The at-send save names the chat (PUT /chat/threads/new answers t1).
  await waitFor(() => expect(H.api.puts.some((p) => /^\/chat\/threads\//.test(p.path))).toBe(true));
}

describe("the answer window draws the chat page compact (36-a)", () => {
  it("only the conversation and the composer: no chat list, no Chat | Code switch, no top bar", async () => {
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatPage />);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    expect(screen.getByTestId("quick-ask-header")).toBeTruthy();
    expect(screen.getByTestId("quick-ask-header").textContent).toContain("Quick Ask");
    expect(screen.getByTestId("quick-ask-continue").textContent).toContain("Continue in the main window");
    expect(screen.queryByTestId("chat-view-switch-row")).toBeNull();
    expect(screen.queryByTestId("chat-rail-aside")).toBeNull();
    expect(screen.queryByTestId("chat-topbar")).toBeNull();
    // The composer is at the bottom from the start: no centred new-chat hero.
    expect(screen.queryByTestId("chat-greeting")).toBeNull();
    expect(screen.queryByTestId("chat-hero-foot")).toBeNull();
    expect(screen.getByTestId("chat-composer")).toBeTruthy();
    // The room fills the small window at every width (no phone-only 60vh cap).
    expect(screen.getByTestId("chat-room").className).toContain("h-[calc(100vh-4.5rem");
    expect(screen.getByTestId("chat-scroll").className).toContain("!max-h-none");
  });

  it("the words go through the ORDINARY stream lane, once, and the chat is saved", async () => {
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatPage />);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    expect(take).toHaveBeenCalledTimes(1);
    const compactBody = H.stream.bodies[0];
    const history = compactBody.messages as { role: string; content: string }[];
    expect(history.at(-1)).toEqual({ role: "user", content: "plain English?" });
    expect(box().value).toBe("");
    // The landing flags are spent; the compact flag stays for a reload.
    expect(window.location.search).not.toContain("quick=");
    expect(window.location.search).not.toContain("new=");
    await act(async () => {
      H.stream.release!({ reply: "It means you pay on the first." });
    });
    await screen.findByText("It means you pay on the first.");
    await waitFor(() => expect(H.api.puts.some((p) => /^\/chat\/threads\//.test(p.path))).toBe(true));

    // The same words asked in the full chat page make the same turn body:
    // Quick Ask is the chat page and its stream lane, not a third lane.
    cleanup();
    H.stream.bodies.length = 0;
    W.ironjarvis!.quickAsk!.isAnswerWindow = false;
    window.history.replaceState({}, "", "/chat?new=1&quick=1");
    render(<ChatPage />);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    const fullBody = H.stream.bodies[0];
    const strip = (b: Record<string, unknown>) => {
      const { turn_id: _t, ...rest } = b;
      return rest;
    };
    expect(strip(compactBody)).toEqual(strip(fullBody));
  });

  it("Continue names the SAVED chat, and waits until there is one", async () => {
    take.mockImplementation(async () => "");
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatPage />);
    await waitFor(() => expect(take).toHaveBeenCalledTimes(1));
    const cont = screen.getByTestId("quick-ask-continue") as HTMLButtonElement;
    // Nothing asked yet, and still no centred new-chat hero: the box is at
    // the bottom of the small window from the first frame.
    expect(screen.queryByTestId("chat-greeting")).toBeNull();
    expect(screen.queryByTestId("chat-hero-foot")).toBeNull();
    // Nothing asked, nothing saved: nothing to continue.
    expect(cont.disabled).toBe(true);
    fireEvent.click(cont);
    expect(toMain).not.toHaveBeenCalled();
    // Ask in the small window's own box.
    fireEvent.change(box(), { target: { value: "and in one line?" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await askedAndSaved();
    await waitFor(() => expect((screen.getByTestId("quick-ask-continue") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId("quick-ask-continue"));
    await waitFor(() => expect(toMain).toHaveBeenCalledTimes(1));
    expect(toMain).toHaveBeenCalledWith({ thread: "t1" });
  });

  it("Esc hides the window, mid-reply too, and the reply is not stopped", async () => {
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatPage />);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    // The reply is still being written (the stream has not been released).
    box().focus();
    fireEvent.keyDown(box(), { key: "Escape" });
    expect(hide).toHaveBeenCalledTimes(1);
    expect(H.stream.aborts).toBe(0);
    expect(H.api.posts.some((p) => /\/stop$/.test(p.path))).toBe(false);
  });

  it("Esc closes an open menu or dialog first, and only then the window", async () => {
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatPage />);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    document.body.appendChild(dialog);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(hide).not.toHaveBeenCalled();
    dialog.remove();
    // A key a handler already took (preventDefault) is not a hide either.
    const taken = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    taken.preventDefault();
    window.dispatchEvent(taken);
    expect(hide).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(hide).toHaveBeenCalledTimes(1);
  });

  it("a link to another page opens in the main window; a chat link stays here", async () => {
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatPage />);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    const door = document.createElement("a");
    door.href = "/schedules?focus=friday#row";
    door.textContent = "Open Schedules";
    screen.getByTestId("chat-scroll").appendChild(door);
    const click = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    door.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    await waitFor(() => expect(toMain).toHaveBeenCalledWith({ path: "/schedules?focus=friday#row" }));
    const chatLink = document.createElement("a");
    chatLink.href = "/chat?thread=t2";
    screen.getByTestId("chat-scroll").appendChild(chatLink);
    const click2 = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    chatLink.dispatchEvent(click2);
    expect(toMain).toHaveBeenCalledTimes(1);
    // Another site is not ours to route: the desktop's guard sends it out.
    const outside = document.createElement("a");
    outside.href = "https://example.com/";
    screen.getByTestId("chat-scroll").appendChild(outside);
    outside.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
    expect(toMain).toHaveBeenCalledTimes(1);
  });

  it("the hotkey pressed while the window is open puts the cursor in its box", async () => {
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatPage />);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    await waitFor(() => expect(focusListeners.length).toBeGreaterThan(0));
    (document.activeElement as HTMLElement | null)?.blur();
    act(() => {
      for (const cb of focusListeners) cb();
    });
    expect(document.activeElement).toBe(box());
  });

  it("a reload of the answer window stays compact: the desktop says which window it is", async () => {
    take.mockImplementation(async () => "");
    window.history.replaceState({}, "", "/chat");
    render(<ChatPage />);
    await screen.findByTestId("quick-ask-header");
    expect(screen.queryByTestId("chat-view-switch-row")).toBeNull();
  });
});

describe("the full chat page is unchanged", () => {
  it("without ?compact=1 and outside the answer window: the switch, the top bar, no Quick Ask header", async () => {
    take.mockImplementation(async () => "");
    W.ironjarvis!.quickAsk!.isAnswerWindow = false;
    W.ironjarvis!.popout = undefined;
    window.history.replaceState({}, "", "/chat");
    render(<ChatPage />);
    await screen.findByTestId("chat-topbar");
    expect(screen.getByTestId("chat-view-switch-row")).toBeTruthy();
    expect(screen.queryByTestId("quick-ask-header")).toBeNull();
    // Esc does not hide anything here.
    fireEvent.keyDown(window, { key: "Escape" });
    expect(hide).not.toHaveBeenCalled();
  });
});

describe("one name, and search finds it", () => {
  it("Help says Quick Ask; Ctrl K finds it by its old word and opens its Handbook section", () => {
    const lines = hotkeyLines({
      window: "Ctrl+Shift+J",
      spotlight: "Ctrl+Shift+Space",
      preferred: { window: "Ctrl+Shift+J", spotlight: "Ctrl+Shift+Space" },
    });
    expect(lines[1].what).toContain("Quick Ask");
    expect(lines.map((l) => l.what).join(" ")).not.toMatch(/quick task/i);
    const palette = readFileSync(join(__dirname, "..", "components", "CommandPalette.tsx"), "utf-8");
    const at = palette.indexOf('id: "deep:quick-ask"');
    expect(at).toBeGreaterThan(-1);
    const row = palette.slice(at, palette.indexOf("},", at));
    expect(row).toContain('"quick task"');
    const section = /section=([a-z0-9-]+)"/.exec(row)?.[1] ?? "";
    const handbook = readFileSync(join(__dirname, "..", "..", "docs", "HANDBOOK.md"), "utf-8").replace(/\r\n/g, "\n");
    const heading = handbook.split("\n").find((l) => l.startsWith("## Quick Ask")) ?? "";
    expect(heading).not.toBe("");
    expect(sectionMatches(headingSlug(heading.replace(/^##\s+/, "")), section)).toBe(true);
  });
});

describe("lib/quickAnswer (pure)", () => {
  it("isCompactChat: the address flag, or the desktop's word", () => {
    W.ironjarvis!.quickAsk!.isAnswerWindow = false;
    expect(isCompactChat("?compact=1")).toBe(true);
    expect(isCompactChat("?compact=0")).toBe(false);
    expect(isCompactChat("")).toBe(false);
    W.ironjarvis!.quickAsk!.isAnswerWindow = true;
    expect(isCompactChat("")).toBe(true);
    delete W.ironjarvis;
    expect(isCompactChat("")).toBe(false);
  });

  it("mainWindowPath: another page of the app goes to the main window; the chat itself stays", () => {
    const here = { origin: "http://localhost:8788", pathname: "/chat" };
    expect(mainWindowPath("/schedules", here)).toBe("/schedules");
    expect(mainWindowPath("/settings?section=a#b", here)).toBe("/settings?section=a#b");
    expect(mainWindowPath("/", here)).toBe("/");
    expect(mainWindowPath("/chat?view=code&session=s1", here)).toBe("/chat?view=code&session=s1");
    expect(mainWindowPath("/chat?ask=hello", here)).toBe("/chat?ask=hello");
    expect(mainWindowPath("/chat?thread=t2", here)).toBeNull();
    expect(mainWindowPath("/chat", here)).toBeNull();
    expect(mainWindowPath("#top", here)).toBeNull();
    expect(mainWindowPath("https://example.com/x", here)).toBeNull();
    expect(mainWindowPath("mailto:a@example.com", here)).toBeNull();
    expect(mainWindowPath("", here)).toBeNull();
  });
});
