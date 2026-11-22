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
 *  - (review) Esc never answers a question card there: the window hides, the
 *    card stays, its buttons answer it, and its keys line names Enter only;
 *  - (second review) except the Local only question, which nothing has sent
 *    or saved yet: Esc is still "Keep it local" there and the window stays;
 *  - (review) the Code view, a coding session and Last night open in the
 *    MAIN window: this one never shows Code;
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
  const stream = {
    bodies: [] as Record<string, unknown>[],
    release: null as null | ((r: Record<string, unknown>) => void),
    aborts: 0,
    // What the turn is waiting on (36-a review: a card in the answer window).
    approval: null as null | { id: string; callId: string; tool: string; args?: Record<string, unknown> },
    mcpAsks: [] as unknown[],
    version: 0,
    listeners: new Set<() => void>(),
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
  };
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
    stream,
    // The app's default model (/health); null = the page's own fallback.
    health: null as null | Record<string, unknown>,
  };
});

vi.mock("@/lib/daemon", async (orig) => {
  const actual = await orig<typeof import("@/lib/daemon")>();
  return {
    ...actual,
    useDaemon: () => {
      const d = actual.useDaemon();
      return H.health ? { ...d, health: H.health as unknown as typeof d.health } : d;
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

vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: false,
        text: "",
        tools: [],
        approval: H.stream.approval,
        mcpAsks: H.stream.mcpAsks,
        run: (body: Record<string, unknown>) => {
          H.stream.bodies.push(body);
          return new Promise<Record<string, unknown>>((res) => {
            H.stream.release = res;
          });
        },
        abort: () => {
          H.stream.aborts += 1;
        },
      };
    },
  };
});
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
import { ChatCodeSurface } from "@/components/chat/ChatCodeSurface";
import { CHAT_VIEW_EVENT, keepView, rememberedView, resetViewMemory } from "@/lib/chatSurface";
import { BUILD_NIGHT_EVENT, BUILD_SESSION_EVENT } from "@/lib/buildLinks";
import { dockAskKeyHint, escAnswersCard, type DockAskItem } from "@/lib/dockAsk";
import { LOCAL_ONLY_KEY } from "@/lib/localOnly";
import { QUICK_ASK_WINDOW_TITLE, QUICK_COMPACT_PARAM, isCompactChat, mainWindowPath } from "@/lib/quickAnswer";
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
  H.stream.approval = null;
  H.stream.mcpAsks = [];
  H.health = null;
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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete W.ironjarvis;
  resetViewMemory();
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
    // Review: the row under the composer gives its room to the messages (no
    // map in the small window), and takes none when empty
    // (e2e/quick-ask-window.spec.ts measures the room).
    expect(screen.queryByTestId("open-conversation-map")).toBeNull();
    expect(screen.getByTestId("composer-meta").className).toContain("empty:hidden");
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

const SHELL_ASK = { id: "apr_1", callId: "c1", tool: "shell", args: { command: "git status" } };
const CITY_ASK = {
  kind: "elicitation",
  id: "e1",
  callId: "c9",
  pack: "Weather",
  message: "Which city?",
  fields: [{ name: "city", type: "string", title: "City", description: "", required: true }],
};

/** The card's keys answer only after its first moment (DOCK_KEY_ARM_MS):
 *  move the clock a minute on instead of waiting. */
function pastTheArmDelay() {
  const t0 = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => t0 + 60_000);
}

describe("a question card in the answer window (36-a review)", () => {
  it("Esc hides the window and never answers the card, with the focus on the card or on the page", async () => {
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatPage />);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    act(() => {
      H.stream.approval = SHELL_ASK;
      H.stream.bump();
    });
    await screen.findByTestId("chat-approval-card");
    const dock = screen.getByTestId("dock-ask");
    pastTheArmDelay();

    // The focus on the card (it takes it when it is shown).
    dock.focus();
    expect(document.activeElement).toBe(dock);
    fireEvent.keyDown(dock, { key: "Escape" });
    expect(hide).toHaveBeenCalledTimes(1);

    // Nothing focused (the page itself).
    dock.blur();
    expect(document.activeElement).toBe(document.body);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(hide).toHaveBeenCalledTimes(2);

    // Neither press answered it: no decision was sent, the card is still up.
    expect(H.api.posts.filter((p) => p.path.startsWith("/chat/approvals/"))).toEqual([]);
    expect(screen.getByTestId("chat-approval-card")).toBeTruthy();
    // Its keys line does not promise an Esc that hides instead.
    expect(screen.getByTestId("composer-key-hint").textContent).toBe("Enter to allow");

    // The card's own button still answers it.
    fireEvent.click(dock.querySelector<HTMLButtonElement>("[data-dock-decline]")!);
    await waitFor(() =>
      expect(H.api.posts).toContainEqual({ path: "/chat/approvals/apr_1", body: { decision: "deny" } }),
    );
  });

  it("Esc from inside an app's question card hides the window too, and never declines", async () => {
    const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatPage />);
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    act(() => {
      H.stream.mcpAsks = [CITY_ASK];
      H.stream.bump();
    });
    const card = await screen.findByTestId("mcp-elicitation-card");
    pastTheArmDelay();
    // The card stops its own keys from bubbling; Esc on its Decline button.
    const decline = card.querySelector<HTMLButtonElement>("[data-dock-decline]")!;
    decline.focus();
    fireEvent.keyDown(decline, { key: "Escape" });
    expect(hide).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls.some((c) => String((c as unknown[])[0]).includes("/chat/mcp/elicitations/"))).toBe(false);
    expect(screen.getByTestId("mcp-elicitation-card")).toBeTruthy();
    expect(screen.getByTestId("composer-key-hint").textContent).toBe("Enter to send");
  });

  // Second review: the Local only question is the exception. It is the page's
  // own, asked BEFORE anything is sent or saved, so neither the chat list nor
  // the bell has it: hiding the window over it lost the words. Esc keeps its
  // meaning there ("Keep it local": nothing sent, nothing recorded, the words
  // back in the box) and the window stays in that press.
  const LOCAL_ROW = { provider: "ollama", model: "qwen3:14b", kind: "local", available: true };
  const CLOUD_ROW = { provider: "claude-cli", model: "claude-opus-4-8", label: "Opus 4.8", kind: "cli", available: true };
  const threadSaves = () => H.api.puts.filter((p) => /^\/chat\/threads\//.test(p.path));

  /** Local only was the user's last choice and the default model would leave
   *  this PC: the quick words meet the Local only question first. */
  async function quickWordsMeetLocalOnly(): Promise<HTMLElement> {
    window.localStorage.setItem(LOCAL_ONLY_KEY, "1");
    H.api.getResponses["/models"] = { models: [LOCAL_ROW, CLOUD_ROW] };
    H.health = { status: "ok", version: "0.0.0", default_provider: "claude-cli", default_model: "claude-opus-4-8", providers: [] };
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatPage />);
    await screen.findByTestId("cloud-ask-card");
    expect(take).toHaveBeenCalledTimes(1);
    // Nothing has been sent or saved while it is up.
    expect(H.stream.bodies).toHaveLength(0);
    expect(threadSaves()).toEqual([]);
    pastTheArmDelay();
    return screen.getByTestId("dock-ask");
  }

  it("the Local only question: Esc on the card keeps it local, the words come back, the window stays", async () => {
    const dock = await quickWordsMeetLocalOnly();
    // Its keys line still names Esc, because Esc still answers it.
    expect(screen.getByTestId("composer-key-hint").textContent).toBe("Enter to send once · Esc to keep it local");
    dock.focus();
    expect(document.activeElement).toBe(dock);
    fireEvent.keyDown(dock, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("cloud-ask-card")).toBeNull());
    expect(hide).not.toHaveBeenCalled();
    expect(box().value).toBe("plain English?");
    expect(H.stream.bodies).toHaveLength(0);
    expect(threadSaves()).toEqual([]);
    // With the question answered, Esc puts the window away as always.
    box().focus();
    fireEvent.keyDown(box(), { key: "Escape" });
    expect(hide).toHaveBeenCalledTimes(1);
  });

  it("the Local only question: Esc with nothing focused keeps it local too, and the window stays", async () => {
    await quickWordsMeetLocalOnly();
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("cloud-ask-card")).toBeNull());
    expect(hide).not.toHaveBeenCalled();
    expect(box().value).toBe("plain English?");
    expect(H.stream.bodies).toHaveLength(0);
  });

  it("the full chat page keeps Esc for the card: it declines there", async () => {
    W.ironjarvis!.quickAsk!.isAnswerWindow = false;
    W.ironjarvis!.popout = undefined;
    take.mockImplementation(async () => "");
    window.history.replaceState({}, "", "/chat");
    render(<ChatPage />);
    const box0 = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box0, { target: { value: "check the repo" } });
    fireEvent.keyDown(box0, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    act(() => {
      H.stream.approval = SHELL_ASK;
      H.stream.bump();
    });
    await screen.findByTestId("chat-approval-card");
    pastTheArmDelay();
    expect(screen.getByTestId("composer-key-hint").textContent).toBe("Enter to allow · Esc to decline");
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() =>
      expect(H.api.posts).toContainEqual({ path: "/chat/approvals/apr_1", body: { decision: "deny" } }),
    );
    expect(hide).not.toHaveBeenCalled();
  });
});

describe("the answer window never shows the Code view (36-a review)", () => {
  const chatOnly = () => <p>the chat</p>;

  it("a window that last showed Code still draws its chat", async () => {
    keepView("code");
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatCodeSurface renderChat={chatOnly} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByTestId("chat-view-panel-chat").hasAttribute("hidden")).toBe(false);
    expect(screen.queryByTestId("chat-view-panel-code")).toBeNull();
  });

  it("Ctrl K's Code view, a coding session's link and Last night open in the MAIN window", async () => {
    window.history.replaceState({}, "", COMPACT_URL);
    render(<ChatCodeSurface renderChat={chatOnly} />);
    act(() => {
      window.dispatchEvent(new CustomEvent(CHAT_VIEW_EVENT, { detail: { view: "code" } }));
    });
    expect(toMain).toHaveBeenLastCalledWith({ path: "/chat?view=code" });
    act(() => {
      window.dispatchEvent(new CustomEvent(BUILD_SESSION_EVENT, { detail: { id: "s1" } }));
    });
    expect(toMain).toHaveBeenLastCalledWith({ path: "/chat?view=code&session=s1" });
    act(() => {
      window.dispatchEvent(new CustomEvent(BUILD_NIGHT_EVENT, { detail: { id: "2026-11-21" } }));
    });
    expect(toMain).toHaveBeenLastCalledWith({ path: "/chat?view=code&night=2026-11-21" });
    expect(toMain).toHaveBeenCalledTimes(3);
    // This window stays on its chat; its address and memory never say Code.
    expect(screen.getByTestId("chat-view-panel-chat").hasAttribute("hidden")).toBe(false);
    expect(screen.queryByTestId("chat-view-panel-code")).toBeNull();
    expect(window.location.search).not.toContain("view=");
    expect(rememberedView()).not.toBe("code");
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
    expect(screen.getByTestId("composer-meta").className).not.toContain("empty:hidden");
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
  it("isCompactChat: in the desktop app the desktop's word alone; in a browser the address flag", () => {
    // 36-a review: `?compact=1` in the MAIN window does not make it one.
    W.ironjarvis!.quickAsk!.isAnswerWindow = false;
    expect(isCompactChat("?compact=1")).toBe(false);
    expect(isCompactChat("")).toBe(false);
    W.ironjarvis!.quickAsk!.isAnswerWindow = true;
    expect(isCompactChat("")).toBe(true);
    expect(isCompactChat("?compact=0")).toBe(true);
    // A browser (no desktop bridge): the address says.
    delete W.ironjarvis;
    expect(isCompactChat("?compact=1")).toBe(true);
    expect(isCompactChat("?compact=0")).toBe(false);
    expect(isCompactChat("")).toBe(false);
  });

  it("dockAskKeyHint names Enter only where Esc does not answer (the answer window)", () => {
    const noEsc = { escAnswers: false };
    expect(dockAskKeyHint("approval", null, noEsc)).toBe("Enter to allow");
    expect(dockAskKeyHint("elicitation", null, noEsc)).toBe("Enter to send");
    expect(dockAskKeyHint("question", null, noEsc)).toBe("Enter to send");
    expect(dockAskKeyHint("cloud", null, noEsc)).toBe("Enter to send once");
    // Everywhere else, word for word as before.
    expect(dockAskKeyHint("approval")).toBe("Enter to allow · Esc to decline");
    expect(dockAskKeyHint("question")).toBe("Enter to send · Esc to skip");
    expect(dockAskKeyHint("cloud")).toBe("Enter to send once · Esc to keep it local");
  });

  it("escAnswersCard: in the answer window Esc answers the Local only question alone", () => {
    const cloud = { kind: "cloud", id: "c1", ask: { provider: "claude-cli", model: "m", name: "Opus 4.8" } } as DockAskItem;
    const approval = { kind: "approval", id: "a1", approval: SHELL_ASK } as unknown as DockAskItem;
    expect(escAnswersCard(cloud, true)).toBe(true);
    expect(escAnswersCard(approval, true)).toBe(false);
    expect(escAnswersCard(null, true)).toBe(false);
    // Everywhere else Esc answers every card, as before.
    expect(escAnswersCard(cloud, false)).toBe(true);
    expect(escAnswersCard(approval, false)).toBe(true);
  });

  it("the answer window's title is the desktop's own: Quick Ask, not Chat", () => {
    const main = readFileSync(join(__dirname, "..", "..", "desktop", "main.js"), "utf-8").replace(/\r\n/g, "\n");
    expect(main).toContain(`title: ${JSON.stringify(QUICK_ASK_WINDOW_TITLE)},`);
  });

  it("a ?compact=1 typed into the main window draws the full chat page", async () => {
    take.mockImplementation(async () => "");
    W.ironjarvis!.quickAsk!.isAnswerWindow = false;
    W.ironjarvis!.popout = undefined;
    window.history.replaceState({}, "", "/chat?compact=1");
    render(<ChatPage />);
    await screen.findByTestId("chat-topbar");
    expect(screen.queryByTestId("quick-ask-header")).toBeNull();
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
