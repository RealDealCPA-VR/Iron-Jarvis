/**
 * v1.328.0 (calm chat W3-1) fix round — the "@" menu fits where it opens, and
 * a chat row says how old it is.
 *
 * 1. On a NEW chat the composer is centred and the chat section that clips the
 *    menu starts just above it: the taller menu (agents + chats) opening upward
 *    lost its first rows off the top, unreachable by mouse (on a phone, the
 *    whole Agents section). `fitAtMenu` measures the room above and below the
 *    card inside the clipping section and picks the side and a height that
 *    keep every row reachable by scrolling inside the menu; a squeezed menu
 *    scrolls as ONE list (no scroll inside a scroll).
 * 2. Saved chats often share a title ("Check what changed in the repo"), so a
 *    row and a picked chip show a quiet age in the chat list's format; the
 *    age is never sent and never saved on the message.
 *
 * Harness: the chat-at-chats-v1328 header. jsdom has no layout, so the tests
 * give the composer card and the chat section boxes of their own.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  AT_MENU_LEAD_ROWS,
  AT_MENU_LEAD_TALL_PX,
  AT_MENU_MAX_PX,
  AT_MENU_MIN_PX,
  fitAtMenu,
  squeezedLeadRows,
} from "@/lib/chatRefsMenuFit";
import { decodeChatRefRows } from "@/lib/chatRefs";

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
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
    refs: { rows: [] as { id: string; title: string; updated_at?: string }[] },
    stream: { bodies: [] as Record<string, unknown>[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    if (path.startsWith("/chat/threads/search-refs")) return { threads: H.refs.rows };
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async () => ({}),
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
      onDelta("Noted.", "Noted.");
      return {
        reply: "Noted.",
        route: { requested: "", provider: "mock", model: "mock", reason: "default" },
      };
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

const MIN = 60_000;
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

/** Boxes by data-testid; everything else has no size (jsdom's default). */
let boxes: Record<string, { top: number; bottom: number }> = {};
const realRect = Element.prototype.getBoundingClientRect;
const realInnerHeight = window.innerHeight;

function rect(top: number, bottom: number): DOMRect {
  return {
    top, bottom, left: 0, right: 600, width: 600, height: bottom - top, x: 0, y: top,
    toJSON: () => ({}),
  } as DOMRect;
}

beforeEach(() => {
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.refs.rows = [
    { id: "c-a", title: "Check what changed in the repo", updated_at: ago(12 * MIN + 5_000) },
    { id: "c-b", title: "Check what changed in the repo", updated_at: ago(3 * 60 * MIN + 5_000) },
  ];
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": {
      agents: [
        { name: "builder", mention: "builder", description: "Builds things", kind: "builtin", healthy: true },
        { name: "planner", mention: "planner", description: "Plans things", kind: "builtin", healthy: true },
      ],
    },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  boxes = {};
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const id = (this as HTMLElement).dataset?.testid;
    const b = id ? boxes[id] : undefined;
    return b ? rect(b.top, b.bottom) : realRect.call(this);
  };
  Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  Element.prototype.getBoundingClientRect = realRect;
  Object.defineProperty(window, "innerHeight", { configurable: true, value: realInnerHeight });
});

async function box(): Promise<HTMLTextAreaElement> {
  return (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
}

/** Give the composer card and the chat section their measured boxes, and
 *  make the section a clipping box (jsdom has no Tailwind). */
function layout(card: [number, number], section: [number, number]) {
  boxes["chat-composer"] = { top: card[0], bottom: card[1] };
  boxes["chat-card"] = { top: section[0], bottom: section[1] };
  (screen.getByTestId("chat-card") as HTMLElement).style.overflowY = "auto";
}

describe("fitAtMenu: the side and height the room allows (v1.328.0)", () => {
  it("an open chat: the card at the bottom, the menu opens upward at full height", () => {
    expect(
      fitAtMenu({ cardTop: 760, cardBottom: 880, clipTop: 116, clipBottom: 884, viewportHeight: 900 }),
    ).toEqual({ side: "above", maxHeight: AT_MENU_MAX_PX });
  });

  it("a new chat on a desk: upward, held to the room above the card", () => {
    // The reviewer's numbers: the section starts at 116; the card at 428.
    const fit = fitAtMenu({ cardTop: 428, cardBottom: 520, clipTop: 116, clipBottom: 884, viewportHeight: 900 });
    expect(fit).toEqual({ side: "above", maxHeight: 428 - 8 - 8 - 116 });
  });

  it("a new chat on a phone: too little room above, so it opens below, held to that room", () => {
    const fit = fitAtMenu({ cardTop: 300, cardBottom: 424, clipTop: 166, clipBottom: 601, viewportHeight: 844 });
    expect(fit).toEqual({ side: "below", maxHeight: 601 - 424 - 8 - 8 });
  });

  it("never reaches past the window, never below a couple of rows, and waits for a laid-out card", () => {
    const fit = fitAtMenu({ cardTop: 100, cardBottom: 200, clipTop: -500, clipBottom: 5000, viewportHeight: 700 });
    expect(fit).toEqual({ side: "below", maxHeight: Math.floor(700 * 0.6) });
    const tight = fitAtMenu({ cardTop: 30, cardBottom: 680, clipTop: 0, clipBottom: 700, viewportHeight: 700 });
    expect(tight?.maxHeight).toBe(AT_MENU_MIN_PX);
    expect(fitAtMenu({ cardTop: 0, cardBottom: 0, clipTop: 0, clipBottom: 0, viewportHeight: 700 })).toBeNull();
  });
});

describe('the "@" menu on the page fits its room (v1.328.0)', () => {
  it("a centred composer with little room above opens the menu BELOW, height held, one scroll", async () => {
    render(<ChatPage />);
    const el = await box();
    layout([300, 424], [166, 601]);
    fireEvent.change(el, { target: { value: "@" } });
    const menu = await screen.findByTestId("at-menu");
    await waitFor(() => expect(menu.getAttribute("data-side")).toBe("below"));
    expect(menu.className).toContain("top-full");
    expect(menu.className).not.toContain("bottom-full");
    expect(menu.style.maxHeight).toBe(`${601 - 424 - 16}px`);
    // Squeezed: the sections give up their own short scrolls.
    await waitFor(() => expect(screen.getAllByTestId("chat-ref-option")).toHaveLength(2));
    const agents = screen.getByRole("listbox", { name: "Agents" });
    expect(agents.className).not.toMatch(/max-h-|overflow-y-auto/);
    expect(screen.getByTestId("at-menu-chats").className).not.toMatch(/max-h-|overflow-y-auto/);
  });

  it("a desk new chat stays upward but no taller than the room above the section's edge", async () => {
    render(<ChatPage />);
    const el = await box();
    layout([428, 520], [116, 884]);
    fireEvent.change(el, { target: { value: "@" } });
    const menu = await screen.findByTestId("at-menu");
    await waitFor(() => expect(menu.style.maxHeight).toBe(`${428 - 16 - 116}px`));
    expect(menu.getAttribute("data-side")).toBe("above");
    expect(menu.className).toContain("bottom-full");
  });

  it("an open chat with room to spare keeps the full menu and each section's own scroll", async () => {
    render(<ChatPage />);
    const el = await box();
    layout([700, 820], [116, 844]);
    fireEvent.change(el, { target: { value: "@" } });
    const menu = await screen.findByTestId("at-menu");
    await waitFor(() => expect(menu.style.maxHeight).toBe(`${AT_MENU_MAX_PX}px`));
    expect(menu.getAttribute("data-side")).toBe("above");
    await waitFor(() => expect(screen.getAllByTestId("chat-ref-option")).toHaveLength(2));
    expect(screen.getByRole("listbox", { name: "Agents" }).className).toContain("overflow-y-auto");
    expect(screen.getByTestId("at-menu-chats").className).toContain("max-h-48");
  });
});

describe("a squeezed menu keeps the Chats section in view (v1.328.0)", () => {
  const NINE = ["builder", "planner", "reviewer", "supervisor", "researcher", "writer", "analyst", "guide", "tester"];
  beforeEach(() => {
    H.api.getResponses["/agents/mentionable"] = {
      agents: NINE.map((n) => ({ name: n, mention: n, description: `The ${n}`, kind: "builtin", healthy: true })),
    };
  });

  const agentRows = () =>
    Array.from(screen.getByRole("listbox", { name: "Agents" }).querySelectorAll('[role="option"]'));

  it("squeezedLeadRows: three rows with some room, one when tight", () => {
    expect(squeezedLeadRows(AT_MENU_LEAD_TALL_PX)).toBe(AT_MENU_LEAD_ROWS);
    expect(squeezedLeadRows(AT_MENU_LEAD_TALL_PX - 1)).toBe(1);
    expect(squeezedLeadRows(AT_MENU_MIN_PX)).toBe(1);
  });

  it("a desk new chat: three agents, then 'N more', then the chats; the keys walk only what is shown", async () => {
    render(<ChatPage />);
    const el = await box();
    layout([428, 520], [116, 884]); // 296px above the card: squeezed
    fireEvent.change(el, { target: { value: "compare with @" } });
    await waitFor(() => expect(screen.getAllByTestId("chat-ref-option")).toHaveLength(2));
    await waitFor(() => expect(agentRows()).toHaveLength(3));
    expect(agentRows().map((r) => r.textContent?.slice(0, 7))).toEqual(["builder", "planner", "reviewe"]);
    expect(screen.getByTestId("at-menu-more-agents").textContent).toBe("6 more agents, type to narrow");
    // The heading still counts them all.
    expect(screen.getByRole("listbox", { name: "Agents" }).textContent).toContain("9 agents");
    // In the menu, the chats come straight after the cut-off agents.
    const menu = screen.getByTestId("at-menu");
    const options = Array.from(menu.querySelectorAll('[role="option"]'));
    expect(options.slice(3).every((o) => o.getAttribute("data-testid") === "chat-ref-option")).toBe(true);
    // ↓ three times: from the first agent to the first chat.
    for (let k = 0; k < 3; k++) fireEvent.keyDown(el, { key: "ArrowDown" });
    await waitFor(() =>
      expect(menu.querySelector('[data-active="true"]')?.getAttribute("data-testid")).toBe("chat-ref-option"),
    );
  });

  it("tight on a phone: one agent and 'N more' before the chats", async () => {
    render(<ChatPage />);
    const el = await box();
    layout([300, 424], [166, 601]); // 161px below the card
    fireEvent.change(el, { target: { value: "@" } });
    await waitFor(() => expect(screen.getAllByTestId("chat-ref-option")).toHaveLength(2));
    await waitFor(() => expect(agentRows()).toHaveLength(1));
    expect(screen.getByTestId("at-menu-more-agents").textContent).toBe("8 more agents, type to narrow");
  });

  it("with no chat matching, or with room to spare, every agent is listed", async () => {
    render(<ChatPage />);
    const el = await box();
    layout([428, 520], [116, 884]);
    fireEvent.change(el, { target: { value: "@er" } }); // agents match, no chat title does
    await waitFor(() => expect(agentRows().length).toBe(NINE.filter((n) => n.includes("er")).length));
    expect(screen.queryByTestId("at-menu-chats")).toBeNull();
    expect(screen.queryByTestId("at-menu-more-agents")).toBeNull();
    // An open chat's room: the full menu, every agent, the chats too.
    layout([700, 820], [116, 844]);
    fireEvent.change(el, { target: { value: "@" } });
    await waitFor(() => expect(screen.getAllByTestId("chat-ref-option")).toHaveLength(2));
    await waitFor(() => expect(agentRows()).toHaveLength(NINE.length));
    expect(screen.queryByTestId("at-menu-more-agents")).toBeNull();
  });
});

describe("a chat row says how old it is (v1.328.0)", () => {
  it("decodes updated_at onto the row, as a string only", () => {
    const rows = decodeChatRefRows({
      threads: [
        { id: "a", title: "A", updated_at: "2026-10-09T11:00:00" },
        { id: "b", title: "B", updated_at: 7 },
        { id: "c", title: "C" },
      ],
    });
    expect(rows).toEqual([
      { id: "a", title: "A", updatedAt: "2026-10-09T11:00:00" },
      { id: "b", title: "B" },
      { id: "c", title: "C" },
    ]);
  });

  it("two chats with one title read apart by age, in the menu and on the chips; only ids are sent", async () => {
    render(<ChatPage />);
    const el = await box();
    fireEvent.change(el, { target: { value: "@" } });
    await waitFor(() =>
      expect(screen.getAllByTestId("chat-ref-age").map((a) => a.textContent)).toEqual(["12m", "3h"]),
    );
    const age = screen.getAllByTestId("chat-ref-age")[0];
    expect(age.className).toContain("text-zinc-500");
    // Pick both; each chip carries its own age.
    fireEvent.click(screen.getAllByTestId("chat-ref-option")[0]);
    await waitFor(() => expect(screen.getAllByTestId("chat-ref-chip")).toHaveLength(1));
    fireEvent.change(el, { target: { value: "@" } });
    await waitFor(() => expect(screen.getAllByTestId("chat-ref-option")).toHaveLength(1));
    fireEvent.click(screen.getAllByTestId("chat-ref-option")[0]);
    await waitFor(() =>
      expect(screen.getAllByTestId("chat-ref-chip-age").map((a) => a.textContent)).toEqual(["12m", "3h"]),
    );
    fireEvent.change(el, { target: { value: "which one?" } });
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    expect(H.stream.bodies[0].thread_refs).toEqual(["c-a", "c-b"]);
    // The saved message keeps id + title only.
    await waitFor(() => {
      const msgs = H.api.puts.at(-1)?.body.messages as { role: string; chatRefs?: unknown }[];
      expect(msgs?.find((m) => m.role === "user")?.chatRefs).toEqual([
        { id: "c-a", title: "Check what changed in the repo" },
        { id: "c-b", title: "Check what changed in the repo" },
      ]);
    });
  });
});
