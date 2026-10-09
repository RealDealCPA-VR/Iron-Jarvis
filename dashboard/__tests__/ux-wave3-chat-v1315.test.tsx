/**
 * v1.315.0 — UX wave 3, track T1 (chat). Written BEFORE the fix.
 *
 * Findings (ux_wave3.json; the verifier's fix_adjustment wins):
 *  - phone-composer-cramped: below sm the composer row WRAPS, the message box
 *    takes the whole first line (basis-full, first), + / project / mic stay
 *    one press away on the line under it with Send pinned right (ml-auto);
 *    the project quick-toggle is icon-only on a phone (its title still names
 *    the project); ALL THREE placeholders (default, talking-to, steer) drop
 *    their keyboard hints on a phone, and a desktop still reaches them.
 *  - jump-pill-on-empty-state: "Jump to latest" never shows over the empty
 *    state, and an empty chat opens at its TOP (the transcript does not
 *    scroll to its bottom sentinel). A thread with messages — including a
 *    first reply still streaming — keeps the pill exactly as today.
 *  - project-tabs-shift-and-not-tabs: Chat/Tasks/Board/Media is a real
 *    tablist ("Project views": role=tab, aria-selected, aria-controls, roving
 *    tabindex + arrow keys), INLINE in the chat card's header (no row pushed
 *    above the card), mirrored above the project surface so Tasks/Board/Media
 *    can switch back; a plain chat has no tablist (no dead row). Chat stays
 *    mounted across a detour (the typed draft survives).
 *  - thread-rail-scope-silent: a project-scoped rail says "Threads in
 *    {project}", the phone's Chats door says "Chats in {project} (n)", the empty
 *    scope says "No chats in this project yet.", and "All chats" widens the
 *    RAIL ONLY — no write to the open thread, the project stays selected — with
 *    a one-press way back ("Only {project}").
 *  - thread-options-invisible-on-touch: the ⋯ is visible without hover
 *    (`[@media(hover:hover)]:opacity-0` + group-hover reveal, focus-within and
 *    the open-menu branch kept), a 28px target, and the project / messaging
 *    chip moved into the meta line in readable sentence case.
 *  - phone-chrome-eats-transcript: Voice is "Voice chat" by name; the header
 *    Project button is icon-only on a phone but NAMED "Project: {name}";
 *    message avatars hide below sm; the persona select carries a visible
 *    "Persona" label. (v1.326.0: the header is the chat top bar, and Voice /
 *    Persona sit in its "⋯" panel, opened first by these tests.)
 *  - carry-approval-browser-list: "Allow for this tab" (and its note) only for
 *    the 14 BUILT-IN browser tools, by exact name from lib/toolWords.ts — an
 *    agent-made `browser_backup` gets every other answer, not the tab one.
 *  - carry-modal-focus-chat-overlays: EmailComposeDialog and the Files panel
 *    preview take focus, keep Tab inside, and give focus back on close —
 *    their dismissal (Escape, backdrop, busy freeze), size, layer and
 *    contents unchanged.
 *
 * Harness: the v1.311.0 chat-wave3 mocks (ChatPage rendered for real; an
 * unmocked GET answers {}), plus a stream that can be HELD (a turn in flight)
 * and a matchMedia stub for phone/desktop widths. jsdom has no layout, so
 * width-dependent behaviour is pinned through the responsive classes and the
 * matchMedia contract; the screenshots verify the pixels.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
      posts: [] as { path: string; body: unknown }[],
      responses: {} as Record<string, unknown>,
      /** When set, every POST stays pending (a submit in flight). */
      holdPosts: false,
    },
    stream: {
      hold: false,
      runs: 0,
    },
    /** Elements scrollIntoView was called on, in order. */
    scrolled: [] as Element[],
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
    H.api.gets.push(path);
    const r = H.api.responses[path];
    if (r === undefined && path.startsWith("/fs/files")) return H.api.responses["/fs/files"] ?? {};
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async (path: string, body?: unknown) => {
    H.api.posts.push({ path, body });
    if (H.api.holdPosts) return new Promise(() => {});
    return {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
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
    run: () => {
      H.stream.runs += 1;
      if (H.stream.hold) return new Promise(() => {});
      return Promise.resolve({ reply: "Noted.", tools_used: [] });
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
// The project surfaces are T3's (components/project/**) and heavy; the chat
// page's contract with them is only "render the view it is told to".
vi.mock("@/components/project/ProjectSurfaces", () => ({
  ProjectSurface: ({ view }: { view: string }) => <div data-testid="surface-stub">surface:{view}</div>,
}));

import ChatPage from "@/app/chat/page";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { EmailComposeDialog } from "@/components/chat/EmailComposeDialog";
import { FilesPanel } from "@/components/terminal/FilesPanel";

/* ------------------------------------------------------------------ data */

const PROJECT_KEY = "ij_chat_project";
const PROJECT = { id: "p1", name: "Q3 Bookkeeping" };
const AT = "2026-10-01T10:00:00Z";

const QUICK = { id: "tq", title: "Quick question", updated_at: AT, messages: 2 };
const EMAIL = {
  id: "te",
  title: "Email draft to client",
  updated_at: AT,
  messages: 3,
  owner: "daemon",
  comm_channel: "telegram",
  comm_display: "Val",
};
const LEDGER = { id: "tp", title: "Ledger totals", project_id: "p1", updated_at: AT, messages: 2 };

const T9 = {
  id: "t9",
  title: "Ledger",
  updated_at: AT,
  messages: [
    { role: "user", content: "total the ledger please" },
    { role: "assistant", content: "The ledger totals 1.2M." },
  ],
};
const TP = {
  id: "tp",
  title: "Ledger totals",
  project_id: "p1",
  updated_at: AT,
  messages: [
    { role: "user", content: "sum the Q3 ledger" },
    { role: "assistant", content: "Q3 ledger sums to 88k." },
  ],
};
const TB = {
  id: "tb",
  title: "Builder chat",
  updated_at: AT,
  messages: [
    { role: "user", content: "build the page" },
    { role: "assistant", content: "Here it is.", panelWho: "builtin:builder", panelThreadId: "athr9" },
  ],
};

/* --------------------------------------------------------------- helpers */

function classes(el: Element | null | undefined): string[] {
  return ((el?.getAttribute("class") ?? "") as string).split(/\s+/).filter(Boolean);
}

/** No ancestor (inclusive) is display-hidden by a bare `hidden` class or [hidden]. */
function isShown(el: Element): boolean {
  for (let n: Element | null = el; n; n = n.parentElement) {
    if (classes(n).includes("hidden") || n.hasAttribute("hidden")) return false;
  }
  return true;
}

async function settle(): Promise<void> {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const CRLF = /\r\n/g;
function src(rel: string): string {
  return readFileSync(resolve(process.cwd(), rel), "utf-8").replace(CRLF, "\n");
}

/** A matchMedia that answers min/max-width and hover queries for `width`. */
function installViewport(width: number) {
  const hover = width >= 1024;
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: width });
  (window as unknown as { matchMedia: (q: string) => MediaQueryList }).matchMedia = (q: string) => {
    const min = /min-width:\s*(\d+(?:\.\d+)?)px/.exec(q);
    const max = /max-width:\s*(\d+(?:\.\d+)?)px/.exec(q);
    let matches = false;
    if (min) matches = width >= Number(min[1]);
    else if (max) matches = width <= Number(max[1]);
    else if (/hover:\s*hover/.test(q)) matches = hover;
    else if (/pointer:\s*fine/.test(q)) matches = hover;
    else if (/pointer:\s*coarse/.test(q)) matches = !hover;
    return {
      matches,
      media: q,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as unknown as MediaQueryList;
  };
}

function withProject(scoped: unknown = { threads: [LEDGER] }) {
  window.localStorage.setItem(PROJECT_KEY, PROJECT.id);
  H.api.responses["/chat/threads?project_id=p1"] = scoped;
}

const composerBox = async () =>
  (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;

/** The transcript scroller: the nearest scrolling column above `el`. */
function scrollerOf(el: Element): HTMLElement {
  for (let n: Element | null = el; n; n = n.parentElement) {
    const c = classes(n);
    if (c.includes("overflow-y-auto") && c.includes("flex-col")) return n as HTMLElement;
  }
  throw new Error("no transcript scroller above the element");
}

function scrollAwayFromBottom(scroller: HTMLElement) {
  Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 2000 });
  Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 500 });
  Object.defineProperty(scroller, "scrollTop", { configurable: true, writable: true, value: 0 });
  fireEvent.scroll(scroller);
}

const jumpPill = () => screen.queryByRole("button", { name: /Jump to latest/ });

function shownTablists(): HTMLElement[] {
  return screen.queryAllByRole("tablist", { name: "Project views" }).filter(isShown);
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.api.posts.length = 0;
  H.api.holdPosts = false;
  H.stream.hold = false;
  H.stream.runs = 0;
  H.scrolled.length = 0;
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [QUICK, EMAIL, LEDGER] },
    "/chat/threads/t9": T9,
    "/chat/threads/tp": TP,
    "/chat/threads/tb": TB,
    "/settings": { settings: {} },
    "/projects": { projects: [PROJECT] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = function scrollIntoView(this: Element) {
    H.scrolled.push(this);
  } as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete (window as unknown as { matchMedia?: unknown }).matchMedia;
});

/* =============================================== phone-composer-cramped */

describe("the composer on a phone (phone-composer-cramped)", () => {
  it("the message box takes the whole first line of the composer card; + / project / mic stay one press away under it", async () => {
    // v1.326.0 (calm chat W1-3): on a NEW chat the project chip sits just
    // above the card (new-chat-centre-v1326 pins that); the toolbar row is a
    // conversation's, so these pins open one.
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
    await screen.findByText("The ledger totals 1.2M.");
    const box = await composerBox();
    // v1.326.0 (calm chat W1-2): the composer is ONE card at EVERY width —
    // the box on top, one toolbar row under it — so the v1.315.0 pins on a
    // row that was one line from sm up (`sm:flex-nowrap`, the box's
    // `basis-full` + `sm:basis-*` pair) moved to the new shape: the box is a
    // full-width child of the card and the TOOLBAR is what wraps on a phone.
    const card = box.parentElement as HTMLElement;
    expect(card.getAttribute("data-testid")).toBe("chat-composer");
    expect(classes(box)).toContain("w-full");
    const toolbar = within(card).getByTestId("composer-toolbar");
    expect(classes(toolbar)).toContain("flex-wrap");
    // Every press is still a direct button in the card's toolbar.
    const menu = within(toolbar).getByRole("button", { name: "Open the chat menu" });
    within(toolbar).getByRole("button", { name: "Switch project" });
    within(toolbar).getByRole("button", { name: "Start dictation" });
    // The box comes first, in the DOM (so on a phone too).
    expect(Boolean(box.compareDocumentPosition(menu) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
  });

  it("Send is pinned to the right end of the controls line", async () => {
    render(<ChatPage />);
    const box = await composerBox();
    fireEvent.change(box, { target: { value: "hello" } });
    const send = await within(box.parentElement as HTMLElement).findByRole("button", { name: "Send" });
    expect(classes(send)).toContain("ml-auto");
  });

  it("the project quick-toggle is icon-only on a phone; its title still names the project", async () => {
    withProject();
    // v1.326.0 (W1-3): the toolbar toggle is a conversation's (a new chat
    // names the project in the chip above the card, at every width).
    window.history.replaceState({}, "", "/chat?thread=tp");
    render(<ChatPage />);
    await screen.findByText("Q3 ledger sums to 88k.");
    const toggle = await screen.findByRole("button", { name: "Switch project" });
    await waitFor(() => expect(toggle.getAttribute("title") ?? "").toContain(PROJECT.name));
    const word = within(toggle).getByText(PROJECT.name);
    expect(classes(word)).toContain("hidden");
    expect(classes(word)).toContain("sm:inline");
  });

  it("on a phone the default placeholder carries no keyboard hints", async () => {
    installViewport(390);
    render(<ChatPage />);
    const box = await composerBox();
    await waitFor(() => expect(box.placeholder).not.toMatch(/Enter|Shift|Esc/));
    expect(box.placeholder).toMatch(/^Message Iron Jarvis/);
  });

  it("on a phone the talking-to placeholder carries no keyboard hints", async () => {
    installViewport(390);
    window.history.replaceState({}, "", "/chat?thread=tb");
    render(<ChatPage />);
    await screen.findByText("Here it is.");
    const box = await composerBox();
    await waitFor(() => expect(box.placeholder).toMatch(/^Message builder/));
    await waitFor(() => expect(box.placeholder).not.toMatch(/Enter|Shift|Esc/));
  });

  it("on a phone the mid-turn steer placeholder carries no keyboard hints", async () => {
    installViewport(390);
    H.stream.hold = true;
    render(<ChatPage />);
    const box = await composerBox();
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.stream.runs).toBe(1));
    await waitFor(() => expect(box.placeholder).toMatch(/^Steer Jarvis mid-turn/));
    expect(box.placeholder).not.toMatch(/Enter|Shift|Esc/);
  });

  it("CONTROL: on a desktop the keyboard hints stay reachable (placeholder or a sm+ hint line)", async () => {
    installViewport(1280);
    render(<ChatPage />);
    const box = await composerBox();
    await waitFor(() => {
      const inPlaceholder = box.placeholder.includes("Enter to send");
      const hintLine = screen
        .queryAllByText(/Enter to send/)
        .some((el) => classes(el).some((c) => /^sm:(block|inline|flex|inline-flex)$/.test(c)));
      expect(inPlaceholder || hintLine).toBe(true);
    });
    expect(box.placeholder).toMatch(/^Message Iron Jarvis/);
  });

  it("CONTROL: the + and project menus still open upward with every row", async () => {
    withProject();
    // v1.326.0 (W1-3): in a conversation, where the project chip is in the
    // toolbar (a new chat's chip above the card opens its menu downward).
    window.history.replaceState({}, "", "/chat?thread=tp");
    render(<ChatPage />);
    await screen.findByText("Q3 ledger sums to 88k.");
    const box = await composerBox();
    const row = box.parentElement as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Open the chat menu" }));
    const attach = await screen.findByText("Attach files or photos");
    expect(classes(attach.closest("div.absolute"))).toContain("bottom-full");
    fireEvent.click(within(row).getByRole("button", { name: "Switch project" }));
    const plain = await screen.findByText("Plain chat — no project");
    const pop = plain.closest("div.absolute") as HTMLElement;
    expect(classes(pop)).toContain("bottom-full");
    expect(within(pop).getByText(PROJECT.name)).toBeTruthy();
    expect(within(pop).getByText(/New project \/ manage all/)).toBeTruthy();
  });
});

/* ============================================= jump-pill-on-empty-state */

describe("Jump to latest only where there is something to jump to (jump-pill-on-empty-state)", () => {
  it("an empty chat scrolled away from its bottom shows no pill", async () => {
    render(<ChatPage />);
    // v1.326.0 (W1-3): the greeting is one line now; it is still in the
    // transcript scroller.
    const lead = await screen.findByText("What can I help with?");
    const scroller = scrollerOf(lead);
    scrollAwayFromBottom(scroller);
    await settle();
    expect(jumpPill()).toBeNull();
  });

  it("an empty chat opens at its top: the transcript never scrolls to its bottom sentinel", async () => {
    render(<ChatPage />);
    // v1.326.0 (W1-3): the greeting is one line now; it is still in the
    // transcript scroller.
    const lead = await screen.findByText("What can I help with?");
    await settle();
    const sentinel = scrollerOf(lead).lastElementChild as Element;
    expect(sentinel).toBeTruthy();
    expect(H.scrolled).not.toContain(sentinel);
  });

  it("CONTROL: a thread with messages still follows its latest message, shows the pill when scrolled up, and jumps", async () => {
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
    const msg = await screen.findByText("The ledger totals 1.2M.");
    const scroller = scrollerOf(msg);
    const sentinel = scroller.lastElementChild as Element;
    await waitFor(() => expect(H.scrolled).toContain(sentinel));
    scrollAwayFromBottom(scroller);
    const pill = await screen.findByRole("button", { name: /Jump to latest/ });
    H.scrolled.length = 0;
    fireEvent.click(pill);
    await waitFor(() => expect(jumpPill()).toBeNull());
    expect(H.scrolled.length).toBeGreaterThan(0);
  });

  it("CONTROL: during the first streamed reply the pill still works", async () => {
    H.stream.hold = true;
    render(<ChatPage />);
    const box = await composerBox();
    fireEvent.change(box, { target: { value: "first question" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.stream.runs).toBe(1));
    const mine = await screen.findByText("first question");
    scrollAwayFromBottom(scrollerOf(mine));
    expect(await screen.findByRole("button", { name: /Jump to latest/ })).toBeTruthy();
  });
});

/* ======================================== project-tabs-shift-and-not-tabs */

describe("the project views are real tabs in the card header (project-tabs-shift-and-not-tabs)", () => {
  it("a project chat shows a 'Project views' tablist INSIDE the chat card, Chat selected, each tab controlling a panel", async () => {
    withProject();
    render(<ChatPage />);
    await screen.findByRole("tablist", { name: "Project views" });
    const lists = shownTablists();
    expect(lists).toHaveLength(1);
    const list = lists[0];
    // No row pushed above the card. v1.326.0 (calm chat): the strip lives in
    // the chat TOP BAR, which every view shares (it was the card's header).
    expect(screen.getByTestId("chat-topbar").contains(list)).toBe(true);
    const tabs = within(list).getAllByRole("tab");
    expect(tabs.map((t) => (t.textContent ?? "").trim().toLowerCase())).toEqual(["chat", "tasks", "board", "media"]);
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false", "false"]);
    for (const t of tabs) expect(t.getAttribute("aria-controls") ?? "").not.toBe("");
    const panelId = tabs[0].getAttribute("aria-controls") as string;
    expect(document.getElementById(panelId)).not.toBeNull();
  });

  it("Tasks swaps in the surface, keeps the chat mounted (draft survives) and a tablist to come back", async () => {
    withProject();
    render(<ChatPage />);
    const box = await composerBox();
    fireEvent.change(box, { target: { value: "draft note" } });
    await screen.findByRole("tablist", { name: "Project views" });
    fireEvent.click(within(shownTablists()[0]).getByRole("tab", { name: /tasks/i }));
    expect(await screen.findByText("surface:tasks")).toBeTruthy();
    expect(classes(screen.getByTestId("chat-card"))).toContain("hidden");
    expect(document.body.contains(box)).toBe(true); // kept mounted
    const lists = shownTablists();
    expect(lists).toHaveLength(1);
    const tasks = within(lists[0]).getByRole("tab", { name: /tasks/i });
    expect(tasks.getAttribute("aria-selected")).toBe("true");
    expect(document.getElementById(tasks.getAttribute("aria-controls") ?? "")).not.toBeNull();
    fireEvent.click(within(lists[0]).getByRole("tab", { name: /chat/i }));
    await waitFor(() => expect(classes(screen.getByTestId("chat-card"))).not.toContain("hidden"));
    expect(screen.queryByText("surface:tasks")).toBeNull();
    expect((await composerBox()).value).toBe("draft note");
  });

  it("arrow keys move focus between tabs (roving tabindex, wrapping); selection waits for a press", async () => {
    withProject();
    render(<ChatPage />);
    await screen.findByRole("tablist", { name: "Project views" });
    const tabs = within(shownTablists()[0]).getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1", "-1", "-1"]);
    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(document.activeElement).toBe(tabs[1]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(tabs[1], { key: "ArrowLeft" });
    expect(document.activeElement).toBe(tabs[0]);
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });
    expect(document.activeElement).toBe(tabs[3]);
    expect(screen.queryByText("surface:media")).toBeNull();
  });

  it("CONTROL: a plain chat has no project tablist (no dead row)", async () => {
    render(<ChatPage />);
    await composerBox();
    await settle();
    expect(screen.queryByRole("tablist", { name: "Project views" })).toBeNull();
  });
});

/* =============================================== thread-rail-scope-silent */

describe("the thread rail says which chats it shows (thread-rail-scope-silent)", () => {
  it("a project-scoped rail reads 'Threads in {project}' and offers 'All chats'", async () => {
    withProject();
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    expect(await within(rail).findByText(/Threads in Q3 Bookkeeping/)).toBeTruthy();
    expect(within(rail).getByRole("button", { name: "All chats" })).toBeTruthy();
  });

  it("'All chats' widens the RAIL only: no write to the open chat, the project stays, and one press goes back", async () => {
    withProject();
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    fireEvent.click(await within(rail).findByTitle("Ledger totals"));
    await screen.findByText("Q3 ledger sums to 88k.");
    await settle();
    const putsBefore = H.api.puts.length;
    fireEvent.click(await within(rail).findByRole("button", { name: "All chats" }));
    expect(await within(rail).findByTitle("Quick question")).toBeTruthy();
    expect(H.api.gets).toContain("/chat/threads");
    await settle();
    // Still unscoped after the dust settles (a refresh honours the rail scope).
    expect(within(rail).getByTitle("Quick question")).toBeTruthy();
    // THE DATA SIDE EFFECT the verifier caught: nothing re-saves the open chat.
    expect(H.api.puts.slice(putsBefore).filter((p) => p.path.startsWith("/chat/threads/"))).toEqual([]);
    expect(window.localStorage.getItem(PROJECT_KEY)).toBe(PROJECT.id);
    expect(screen.getByRole("button", { name: "Switch project" }).getAttribute("title") ?? "").toContain(
      PROJECT.name,
    );
    expect(screen.getByText("Q3 ledger sums to 88k.")).toBeTruthy();
    // The header no longer claims a scope it is not showing.
    expect(within(rail).queryByText(/Threads in Q3 Bookkeeping/)).toBeNull();
    // One press back to the project's chats.
    const back = within(rail).getByRole("button", { name: /^(Only|Back to|Just|Show only) (Q3 Bookkeeping|this project)$/i });
    fireEvent.click(back);
    await waitFor(() => expect(within(rail).queryByTitle("Quick question")).toBeNull());
    expect(await within(rail).findByText(/Threads in Q3 Bookkeeping/)).toBeTruthy();
  });

  it("an empty project scope says the project has no chats yet", async () => {
    withProject({ threads: [] });
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    expect(await within(rail).findByText("No chats in this project yet.")).toBeTruthy();
  });

  // v1.329.0 (calm chat W4 F2): the boxy in-page toggle is gone; the phone's
  // door to the list is the top bar's quiet "Chats" (it opens the nav drawer,
  // which holds this same rail). It keeps naming its scope.
  it("the phone's Chats door names the scope: 'Chats in {project} (n)'", async () => {
    withProject();
    render(<ChatPage />);
    expect(await screen.findByRole("button", { name: /Chats in Q3 Bookkeeping \(1\)/ })).toBeTruthy();
  });

  it("CONTROL: no project — 'Threads', no 'All chats', 'Chats (3)', the full-list empty wording kept", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Quick question");
    expect(within(rail).getByText("Threads", { exact: true })).toBeTruthy();
    expect(within(rail).queryByRole("button", { name: "All chats" })).toBeNull();
    expect(screen.getByRole("button", { name: /^Chats \(3\)$/ })).toBeTruthy();
    cleanup();
    H.api.responses["/chat/threads"] = { threads: [] };
    render(<ChatPage />);
    const rail2 = await screen.findByTestId("chat-thread-rail");
    expect(await within(rail2).findByText(/No saved chats yet/)).toBeTruthy();
  });
});

/* ===================================== thread-options-invisible-on-touch */

describe("thread options are reachable on touch (thread-options-invisible-on-touch)", () => {
  async function optionButtons() {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Quick question");
    return within(rail).getAllByRole("button", { name: /^Options for / });
  }

  it("the ⋯ is visible without hover; only hover-capable screens hide it until hover", async () => {
    const buttons = await optionButtons();
    expect(buttons).toHaveLength(3);
    for (const b of buttons) {
      const cls = [...classes(b), ...classes(b.parentElement)];
      expect(cls).not.toContain("opacity-0");
      expect(cls).toContain("[@media(hover:hover)]:opacity-0");
      expect(cls).toContain("[@media(hover:hover)]:group-hover/thread:opacity-100");
      expect(cls).toContain("focus-within:opacity-100");
    }
  });

  it("the ⋯ is a 28px+ target", async () => {
    const [b] = await optionButtons();
    expect(classes(b).some((c) => /^(min-)?h-(7|8|9|10|11|\[(2[8-9]|[3-9]\d)px\])$/.test(c))).toBe(true);
    expect(classes(b).some((c) => /^(min-)?w-(7|8|9|10|11|\[(2[8-9]|[3-9]\d)px\])$/.test(c))).toBe(true);
  });

  // v1.327.0 (calm chat W2-1): the list is GROUPED under projects, and a row
  // is a status dot, the title and a short age — the "6h ago · 2 msgs ·
  // {project}" meta line is gone. A project chat is named by its group's
  // heading now (the readable, uncapped part of this pin is unchanged: the
  // row itself carries no uppercase and no capped chip); a messaging chat's
  // channel rides beside the title, with its cue.
  it("a project chat sits under its project's heading, the row readable and uncapped", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    const row = await within(rail).findByTitle("Ledger totals");
    await waitFor(() =>
      expect(row.closest("section")?.querySelector("h3")?.textContent).toBe(PROJECT.name),
    );
    expect(row.querySelectorAll(".uppercase")).toHaveLength(0);
    expect(row.innerHTML).not.toContain("max-w-[5.5rem]");
  });

  it("a messaging chat names its channel beside its title", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    const row = await within(rail).findByTitle("Email draft to client");
    expect(row.textContent ?? "").toMatch(/telegram/i);
    expect(row.querySelectorAll(".uppercase")).toHaveLength(0);
    // The "this is a messaging thread" cue stays reachable.
    expect(row.querySelector('[title="Messaging thread"]')).not.toBeNull();
  });

  it("CONTROL: the same menu, the open-menu reveal, and title room clear of the ⋯", async () => {
    await optionButtons();
    // v1.327.0: groups reorder the rows (the project's group first), so the
    // ⋯ is picked by its chat's name rather than by position.
    const b = screen.getByRole("button", { name: "Options for Quick question" });
    const row = b.parentElement?.parentElement as HTMLElement;
    const open = within(row).getByTitle("Quick question");
    expect(classes(open).some((c) => /^pr-(9|10|11|12)$/.test(c))).toBe(true);
    fireEvent.click(b);
    const menu = await screen.findByRole("menu", { name: /^Options for / });
    for (const label of [/Rename/, /Pin to top/, /Turn into workflow/, /Add to project/, /Delete chat/]) {
      expect(within(menu).getByText(label)).toBeTruthy();
    }
    expect([...classes(b), ...classes(b.parentElement)]).toContain("opacity-100");
  });
});

/* ======================================== phone-chrome-eats-transcript */

/** v1.326.0 (calm chat): the header row became the chat top bar; Voice, read
 *  aloud, Persona + its editor and the project panel toggle live in its "⋯"
 *  panel. Open it the way a user does. */
async function openMoreMenu(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: "More chat options" }));
  return await screen.findByRole("group", { name: "Chat options" });
}

describe("the chat top bar fits a phone (phone-chrome-eats-transcript)", () => {
  it("Voice is named 'Voice chat' (in the ⋯ panel, where its word always shows)", async () => {
    render(<ChatPage />);
    const panel = await openMoreMenu();
    const voice = within(panel).getByRole("button", { name: /^Voice chat$/i });
    expect(voice.hasAttribute("aria-pressed")).toBe(true);
    const word = within(voice).getByText(/^Voice chat$/);
    expect(classes(word)).not.toContain("hidden");
  });

  it("the top bar's Project button is icon-only on a phone but NAMED 'Project: {name}'", async () => {
    withProject();
    render(<ChatPage />);
    const bar = await screen.findByTestId("chat-topbar");
    const btn = await within(bar).findByRole("button", { name: /^Project: Q3 Bookkeeping/ });
    // It opens a drawer now: expanded/collapsed, not pressed.
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    const word = within(btn).getByText("Project");
    expect(classes(word)).toContain("hidden");
    expect(classes(word)).toContain("sm:inline");
  });

  // Calm chat W1-4 (v1.326.0) went further than v1.315.0's "hide below sm":
  // there are no avatar tiles at ANY width now (the reply is prose, your own
  // message a tinted bubble on the right), so a phone line loses nothing.
  it("messages carry no avatar tile at any width", async () => {
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
    const msg = await screen.findByText("The ledger totals 1.2M.");
    const row = msg.closest("[data-msg-index]");
    expect(row).not.toBeNull();
    let avatar: Element | null = null;
    for (let n: Element | null = msg; n && n !== row?.parentElement && !avatar; n = n.parentElement) {
      const first = n.firstElementChild;
      if (classes(n).includes("gap-3") && first?.tagName === "SPAN" && first.querySelector("svg")) avatar = first;
    }
    expect(avatar).toBeNull();
  });

  it("the persona select carries a visible 'Persona' label", async () => {
    render(<ChatPage />);
    await openMoreMenu();
    const sel = (await screen.findByRole("combobox", { name: "Persona" })) as HTMLSelectElement;
    const byFor = sel.id ? document.querySelector(`label[for="${sel.id}"]`) : null;
    const byRef = (sel.getAttribute("aria-labelledby") ?? "")
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => document.getElementById(id))
      .find(Boolean);
    const label = byFor ?? byRef ?? null;
    expect(label).not.toBeNull();
    expect(label!.textContent).toMatch(/Persona/);
    expect(classes(label)).not.toContain("sr-only");
  });

  it("CONTROL: every header control is still there with its action", async () => {
    render(<ChatPage />);
    const panel = await openMoreMenu();
    const sel = (await within(panel).findByRole("combobox", { name: "Persona" })) as HTMLSelectElement;
    expect(Array.from(sel.options).some((o) => o.textContent === "+ New persona…")).toBe(true);
    expect(within(panel).getByRole("button", { name: "Modify persona" })).toBeTruthy();
    expect(
      within(panel)
        .getAllByRole("button")
        .some((b) => b.hasAttribute("aria-pressed") && /project/i.test(`${b.getAttribute("aria-label") ?? ""} ${b.textContent ?? ""} ${b.getAttribute("title") ?? ""}`)),
    ).toBe(true);
  });
});

/* ============================================ carry-approval-browser-list */

const PY_BROWSER_TOOLS = Array.from(
  src("../src/iron_jarvis/browser/tools.py").matchAll(/^\s+name = "(browser_[a-z_]+)"$/gm),
).map((m) => m[1]);

describe("the tab answer is for the BUILT-IN browser tools only (carry-approval-browser-list)", () => {
  it("an agent-made browser_backup gets every other answer but not 'Allow for this tab'", () => {
    render(<ApprovalCard approval={{ id: "apr_x", callId: "c1", tool: "browser_backup", args: {} }} />);
    expect(screen.queryByTestId("approval-tab")).toBeNull();
    expect(screen.queryByTestId("approval-tab-note")).toBeNull();
    expect(screen.getByRole("button", { name: "Allow once" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Allow for this conversation" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
  });

  it("CONTROL: every built-in browser tool keeps the tab answer and its note", () => {
    expect(PY_BROWSER_TOOLS).toHaveLength(14);
    for (const tool of PY_BROWSER_TOOLS) {
      render(<ApprovalCard approval={{ id: `apr_${tool}`, callId: "c1", tool, args: {} }} />);
      expect(screen.getByTestId("approval-tab").textContent).toContain("Allow for this tab");
      expect(screen.getByTestId("approval-tab-note").textContent).toMatch(/until you close/);
      cleanup();
    }
  });

  it("lib/toolWords.ts exports isBuiltinBrowserTool: exactly the 14 names in browser/tools.py", async () => {
    const spec = ["@/lib", "toolWords"].join("/");
    const mod = (await import(/* @vite-ignore */ spec)) as { isBuiltinBrowserTool?: (t: string) => boolean };
    expect(typeof mod.isBuiltinBrowserTool).toBe("function");
    for (const t of PY_BROWSER_TOOLS) expect(mod.isBuiltinBrowserTool!(t)).toBe(true);
    for (const t of ["browser_backup", "browser_", "Browser_click", "", "shell", "write_file"]) {
      expect(mod.isBuiltinBrowserTool!(t)).toBe(false);
    }
  });

  it("ApprovalCard asks toolWords, never a browser_ prefix", () => {
    const code = src("components/chat/ApprovalCard.tsx");
    expect(code).not.toMatch(/startsWith\(\s*["']browser_["']\s*\)/);
    expect(code).toMatch(/from\s+["']@\/lib\/toolWords["']/);
    expect(code).toContain("isBuiltinBrowserTool");
  });
});

/* =========================================== carry-modal-focus-chat-overlays */

function ComposeHarness({ onClosed }: { onClosed?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open compose</button>
      {open && (
        <EmailComposeDialog
          mode="draft"
          to={[]}
          cc={[]}
          files={[]}
          bodyText=""
          getBody={() => ({ html: "<p>Hi</p>", text: "Hi" })}
          onClose={() => {
            onClosed?.();
            setOpen(false);
          }}
          onDone={() => {}}
        />
      )}
    </>
  );
}

async function openCompose(): Promise<{ opener: HTMLElement; dialog: HTMLElement }> {
  const opener = screen.getByRole("button", { name: "Open compose" });
  opener.focus();
  fireEvent.click(opener);
  const dialog = await screen.findByRole("dialog", { name: "Save to Drafts" });
  return { opener, dialog };
}

/** The scrim the dialog sits on: the dialog itself when it IS the fixed overlay, else its parent. */
function backdropOf(dialog: HTMLElement): HTMLElement {
  return classes(dialog).includes("fixed") ? dialog : (dialog.parentElement as HTMLElement);
}

describe("EmailComposeDialog gets Modal focus behaviour (carry-modal-focus-chat-overlays)", () => {
  it("with no email account: focus moves in, Tab wraps, Escape gives focus back to the opener", async () => {
    H.api.responses["/comm/channels"] = { channels: [] };
    render(<ComposeHarness />);
    const { opener, dialog } = await openCompose();
    await within(dialog).findByText("No email account is connected yet.");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    const last = within(dialog).getByRole("link", { name: "Open Channels" });
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it("with an account: the To field keeps its autofocus, Tab wraps from the last control, focus returns on close", async () => {
    H.api.responses["/comm/channels"] = { channels: [{ name: "work-email", type: "email" }] };
    render(<ComposeHarness />);
    const { opener, dialog } = await openCompose();
    const submit = await within(dialog).findByTestId("compose-submit");
    await waitFor(() => expect(document.activeElement).toBe(dialog.querySelector("#compose-to")));
    submit.focus();
    fireEvent.keyDown(submit, { key: "Tab" });
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it("CONTROL: same size, same choices, same notes; backdrop closes, a click inside does not", async () => {
    H.api.responses["/comm/channels"] = { channels: [{ name: "work-email", type: "email" }] };
    const closed = vi.fn();
    render(<ComposeHarness onClosed={closed} />);
    const { dialog } = await openCompose();
    await within(dialog).findByTestId("compose-submit");
    expect(classes(dialog)).toContain("max-w-lg");
    expect(within(dialog).getByRole("radiogroup", { name: "What to do with this email" })).toBeTruthy();
    expect(within(dialog).getByTestId("compose-note").textContent).toMatch(/nothing is sent/);
    fireEvent.click(within(dialog).getByTestId("compose-mode-send"));
    expect(within(dialog).getByTestId("compose-note").textContent).toMatch(/can't be undone/);
    fireEvent.click(within(dialog).getByTestId("compose-note"));
    expect(closed).not.toHaveBeenCalled();
    fireEvent.click(backdropOf(dialog));
    expect(closed).toHaveBeenCalledTimes(1);
  });

  it("CONTROL: a submit in flight freezes Escape and the backdrop", async () => {
    H.api.responses["/comm/channels"] = { channels: [{ name: "work-email", type: "email" }] };
    H.api.holdPosts = true;
    const closed = vi.fn();
    render(<ComposeHarness onClosed={closed} />);
    const { dialog } = await openCompose();
    fireEvent.click(await within(dialog).findByTestId("compose-submit"));
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/comm/email/compose")).toBe(true));
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(backdropOf(dialog));
    expect(closed).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Save to Drafts" })).toBeTruthy();
  });
});

describe("the Files panel preview gets Modal focus behaviour (carry-modal-focus-chat-overlays)", () => {
  const FILE = { name: "report.bin", path: "C:/work/report.bin", rel: "report.bin", size: 2048, mtime: 1_700_000_000 };
  beforeEach(() => {
    H.api.responses["/fs/files"] = { root: "C:/work", files: [FILE], count: 1, truncated: false };
  });

  async function openPreview(): Promise<{ row: HTMLElement; dialog: HTMLElement }> {
    render(<FilesPanel folder="C:/work" />);
    const row = await screen.findByTitle(FILE.path);
    row.focus();
    fireEvent.click(row);
    const dialog = await screen.findByRole("dialog", { name: FILE.name });
    return { row, dialog };
  }

  it("focus moves into the preview, Tab wraps, Escape gives focus back to the file row", async () => {
    const { row, dialog } = await openPreview();
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    const last = within(dialog).getByRole("link", { name: /Open/ });
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Copy full path" }));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(row));
  });

  it("CONTROL: same layer (80), same size, same contents; backdrop closes, a click inside does not", async () => {
    const { dialog } = await openPreview();
    let z = false;
    for (let n: HTMLElement | null = dialog; n && n !== document.body; n = n.parentElement) {
      if (n.style.zIndex === "80" || classes(n).includes("z-[80]")) z = true;
    }
    expect(z).toBe(true);
    expect(dialog.matches(".max-w-3xl") || dialog.querySelector(".max-w-3xl") !== null).toBe(true);
    expect(within(dialog).getByRole("button", { name: "Copy full path" })).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeTruthy();
    expect(within(dialog).getAllByText(FILE.path).length).toBeGreaterThan(0);
    fireEvent.click(within(dialog).getByText("No inline preview for this file type."));
    expect(screen.getByRole("dialog", { name: FILE.name })).toBeTruthy();
    fireEvent.click(backdropOf(dialog));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
