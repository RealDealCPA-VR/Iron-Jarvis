/**
 * v1.326.0, Calm chat W1-3: a new chat puts the composer in the middle.
 *
 * On an empty conversation (no messages, nothing running) the chat screen is
 * one centred group, slightly above true centre: one line of greeting, the
 * project chip just above the card, the composer card, then (quietly, under
 * the card) the preflight warning, the connect doors while replies are a demo,
 * exactly three suggestion pills and the whole-job ideas folded behind "More
 * ideas". After the first message the SAME composer element sits in the
 * bottom dock: only its containers change, so it is never remounted (focus
 * and typed text survive). The glide respects prefers-reduced-motion.
 *
 * Harness: the composer-card-v1326 mocks (ChatPage rendered for real; an
 * unmocked GET answers {}), plus a daemon whose /health the test sets (for the
 * connect doors) and a provider-health hook the test sets (for the preflight
 * note). jsdom has no layout, so the centring is pinned through the spacers'
 * grow factors and the classes; the screenshots verify the pixels.
 */

import type { ReactNode } from "react";
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
      responses: {} as Record<string, unknown>,
    },
    stream: { hold: false, runs: 0 },
    health: null as Record<string, unknown> | null,
    prov: {
      byProvider: {} as Record<string, boolean>,
      defaultProvider: "",
    },
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
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
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
    refresh: () => {},
  }),
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
    byProvider: H.prov.byProvider,
    signedOutByProvider: {},
    defaultProvider: H.prov.defaultProvider,
    loading: false,
    stale: false,
    refresh: () => {},
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
import { CHAT_EXAMPLES } from "@/components/chat/examples";
import { NewChatSuggestions } from "@/components/chat/NewChatSuggestions";

/* ------------------------------------------------------------------ data */

const AT = "2026-10-01T10:00:00Z";
const PROJECT = { id: "p1", name: "Q3 Bookkeeping" };
const SAVED = {
  id: "ta",
  title: "Ledger chat",
  updated_at: AT,
  messages: [
    { role: "user", content: "read the ledger" },
    { role: "assistant", content: "The ledger is read." },
  ],
};

const NOTHING_CONNECTED = [
  { provider: "anthropic", available: false, class: "api", inherited_from: null },
  { provider: "openai", available: false, class: "api", inherited_from: null },
  { provider: "claude-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "codex-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "ollama", available: false, class: "local", inherited_from: null },
];

/* --------------------------------------------------------------- helpers */

function classes(el: Element | null | undefined): string[] {
  return ((el?.getAttribute("class") ?? "") as string).split(/\s+/).filter(Boolean);
}

/** A class that paints a fill AT REST (a hover/focus/variant fill is fine). */
function restingFills(el: Element): string[] {
  return classes(el).filter((c) => /^bg-/.test(c) && c !== "bg-transparent");
}

const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

const box = async () => (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
const card = () => screen.getByTestId("chat-composer");
const toolbar = () => screen.getByTestId("composer-toolbar");
const grow = (testid: string) => Number(screen.getByTestId(testid).style.flexGrow || "0");

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.stream.hold = false;
  H.stream.runs = 0;
  H.health = null;
  H.prov.byProvider = {};
  H.prov.defaultProvider = "";
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/chat/threads/ta": SAVED,
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
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------ new chat */

describe("a new chat: one centred group", () => {
  it("greeting, then the project chip, then the card, then three quiet suggestions", async () => {
    render(<ChatPage />);
    const b = await box();
    const greeting = screen.getByTestId("chat-greeting");
    expect(greeting.textContent).toContain("What can I help with?");
    // One line: the long sub-sentence is gone.
    expect(screen.queryByText(/Start a conversation/)).toBeNull();

    // The project chip is just above the card, not inside it.
    const chip = screen.getByRole("button", { name: "Switch project" });
    expect(screen.getByTestId("chat-hero-project").contains(chip)).toBe(true);
    expect(card().contains(chip)).toBe(false);
    expect(within(toolbar()).queryByRole("button", { name: "Switch project" })).toBeNull();
    expect(follows(greeting, chip)).toBe(true);
    expect(follows(chip, card())).toBe(true);
    expect(b.parentElement).toBe(card());

    // Exactly three suggestions, under the card, anchor first.
    const foot = screen.getByTestId("chat-hero-foot");
    expect(follows(card(), foot)).toBe(true);
    const pills = within(screen.getByTestId("chat-suggestions")).getAllByRole("button");
    expect(pills).toHaveLength(3);
    expect(pills[0].textContent).toBe(CHAT_EXAMPLES[0]);
    for (const p of pills) {
      expect(CHAT_EXAMPLES).toContain(p.textContent);
      // A pill: hairline edge, muted text, filled only on hover.
      expect(classes(p)).toEqual(expect.arrayContaining(["rounded-full", "border", "text-zinc-400", "hover:bg-white/[0.06]"]));
      expect(restingFills(p)).toEqual([]);
      // Whole-pixel text sizes only.
      expect(classes(p).filter((c) => /^text-\[\d+\.\d+px\]$/.test(c))).toEqual([]);
    }
  });

  it("the group is centred a little above the middle; the transcript takes only the greeting's height", async () => {
    render(<ChatPage />);
    await box();
    const top = grow("chat-hero-spacer-top");
    const bottom = grow("chat-hero-spacer-bottom");
    expect(top).toBeGreaterThan(0);
    // More room under than over: the card sits slightly above true centre.
    expect(bottom).toBeGreaterThan(top);
    const dock = screen.getByTestId("chat-dock");
    const scroller = dock.previousElementSibling as HTMLElement;
    expect(classes(scroller)).toContain("flex-none");
    expect(classes(scroller)).not.toContain("flex-1");
    // Nothing to fade on a new chat: the greeting is not masked.
    expect(scroller.className).not.toContain("mask-image");
    // The spacers sit around the group: above the transcript, under the dock.
    expect(scroller.previousElementSibling?.getAttribute("data-testid")).toBe("chat-hero-spacer-top");
    expect(dock.nextElementSibling?.getAttribute("data-testid")).toBe("chat-hero-spacer-bottom");
    // A tall new chat (doors, ideas) scrolls as a whole on a short screen.
    // v1.328.0: from md; below md the page scrolls and the section must not
    // clip the composer's "@" menu.
    expect(classes(screen.getByTestId("chat-card"))).toContain("md:overflow-y-auto");
    expect(classes(screen.getByTestId("chat-card"))).not.toContain("overflow-y-auto");
    // The glide honours prefers-reduced-motion.
    for (const id of ["chat-hero-spacer-top", "chat-hero-spacer-bottom"]) {
      expect(classes(screen.getByTestId(id))).toEqual(
        expect.arrayContaining(["transition-[flex-grow]", "motion-reduce:transition-none"]),
      );
    }
    // No disabled Map pointing at questions nobody asked.
    expect(screen.queryByTestId("open-conversation-map")).toBeNull();
  });

  it("a suggestion fills the box and sends nothing", async () => {
    render(<ChatPage />);
    const b = await box();
    const pill = within(screen.getByTestId("chat-suggestions")).getAllByRole("button")[0];
    fireEvent.click(pill);
    await waitFor(() => expect(b.value).toBe(CHAT_EXAMPLES[0]));
    expect(H.stream.runs).toBe(0);
  });

  it("the whole-job ideas are one press away under the suggestions", async () => {
    render(<ChatPage />);
    await box();
    expect(screen.queryByText(/or start a whole job/i)).toBeNull();
    const more = screen.getByTestId("chat-more-ideas");
    expect(more.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more);
    expect(await screen.findByText(/or start a whole job/i)).toBeTruthy();
    expect(more.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("chat-hero-foot").contains(screen.getByText(/or start a whole job/i))).toBe(true);
  });

  it("the chip above the card names the project and picks one from its menu, which opens downward", async () => {
    render(<ChatPage />);
    await box();
    const chip = screen.getByRole("button", { name: "Switch project" });
    expect(chip.textContent).toContain("No project");
    fireEvent.click(chip);
    const plain = await screen.findByText("Plain chat — no project");
    const pop = plain.closest("div.absolute") as HTMLElement;
    expect(classes(pop)).toContain("top-full");
    fireEvent.click(within(pop).getByText(PROJECT.name));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Switch project" }).textContent).toContain(PROJECT.name),
    );
    expect(window.localStorage.getItem("ij_chat_project")).toBe(PROJECT.id);
    // Still above the card, and the name shows at every width (no `hidden`).
    const again = screen.getByRole("button", { name: "Switch project" });
    expect(screen.getByTestId("chat-hero-project").contains(again)).toBe(true);
    expect(classes(within(again).getByText(PROJECT.name))).not.toContain("hidden");
  });

  it("the preflight warning sits under the card on a new chat, in the tray in a conversation", async () => {
    H.prov.defaultProvider = "ollama";
    H.prov.byProvider = { ollama: false };
    render(<ChatPage />);
    await box();
    const note = await screen.findByTestId("ij-preflight-note");
    expect(screen.getByTestId("chat-hero-foot").contains(note)).toBe(true);
    expect(screen.getByTestId("composer-notices").contains(note)).toBe(false);
    expect(screen.getAllByTestId("ij-preflight-note")).toHaveLength(1);

    cleanup();
    window.history.replaceState({}, "", "/chat?thread=ta");
    render(<ChatPage />);
    await screen.findByText("The ledger is read.");
    const tray = screen.getByTestId("composer-notices");
    await waitFor(() => expect(tray.contains(screen.getByTestId("ij-preflight-note"))).toBe(true));
    expect(screen.queryByTestId("chat-hero-foot")).toBeNull();
  });

  it("while replies are a demo, the connect doors sit under the card and before the suggestions", async () => {
    H.health = {
      status: "ok",
      version: "1.326.0",
      default_provider: "mock",
      default_model: "",
      providers: NOTHING_CONNECTED,
    };
    render(<ChatPage />);
    await box();
    const sub = await screen.findByRole("button", { name: /I already pay/ });
    const foot = screen.getByTestId("chat-hero-foot");
    expect(foot.contains(sub)).toBe(true);
    expect(follows(card(), sub)).toBe(true);
    expect(follows(sub, screen.getByTestId("chat-suggestions"))).toBe(true);
    // Quiet: the doors' heading has no box of its own around it.
    const heading = screen.getByText("Connect a model for real answers");
    expect(restingFills(heading.parentElement as HTMLElement)).toEqual([]);
    expect(classes(heading.parentElement).filter((c) => /^border/.test(c))).toEqual([]);
  });
});

/* ------------------------------------------------- after the first message */

describe("after the first message the same composer sits in the dock", () => {
  it("the box is the SAME element (never remounted) and keeps focus; the new-chat pieces go", async () => {
    render(<ChatPage />);
    const b = await box();
    b.focus();
    expect(document.activeElement).toBe(b);
    fireEvent.change(b, { target: { value: "hello there" } });
    fireEvent.keyDown(b, { key: "Enter" });
    await screen.findByText("Noted.");
    await waitFor(() => expect(screen.queryByTestId("chat-hero-foot")).toBeNull());
    expect(screen.getByRole("textbox", { name: "Message" })).toBe(b);
    expect(document.activeElement).toBe(b);
    expect(screen.queryByTestId("chat-greeting")).toBeNull();
    expect(screen.queryByTestId("chat-hero-project")).toBeNull();
    // The project chip is back in the card's toolbar.
    within(toolbar()).getByRole("button", { name: "Switch project" });
    // The spacers fold away and the transcript takes the room again.
    expect(grow("chat-hero-spacer-top")).toBe(0);
    expect(grow("chat-hero-spacer-bottom")).toBe(0);
    const scroller = screen.getByTestId("chat-dock").previousElementSibling as HTMLElement;
    expect(classes(scroller)).toContain("flex-1");
    expect(classes(screen.getByTestId("chat-card"))).toContain("overflow-hidden");
    screen.getByTestId("open-conversation-map");
  });

  it("text typed while the first reply is still coming stays in the same box", async () => {
    H.stream.hold = true;
    render(<ChatPage />);
    const b = await box();
    fireEvent.change(b, { target: { value: "first question" } });
    fireEvent.keyDown(b, { key: "Enter" });
    // Busy: the dock layout is in force, the box is the same element.
    await waitFor(() => expect(screen.queryByTestId("chat-hero-foot")).toBeNull());
    expect(screen.getByRole("textbox", { name: "Message" })).toBe(b);
    fireEvent.change(b, { target: { value: "a follow-up I am drafting" } });
    expect((screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement).value).toBe(
      "a follow-up I am drafting",
    );
  });

  it("a saved conversation opens with the composer docked and no new-chat pieces", async () => {
    window.history.replaceState({}, "", "/chat?thread=ta");
    render(<ChatPage />);
    await screen.findByText("The ledger is read.");
    expect(screen.queryByTestId("chat-hero-foot")).toBeNull();
    expect(screen.queryByTestId("chat-suggestions")).toBeNull();
    within(toolbar()).getByRole("button", { name: "Switch project" });
  });
});

/* ------------------------------------------------- the component alone */

describe("NewChatSuggestions", () => {
  it("shows at most three pills whatever it is given, and each press hands its words back", () => {
    const onPick = vi.fn();
    render(<NewChatSuggestions examples={CHAT_EXAMPLES} onPick={onPick} />);
    const pills = within(screen.getByTestId("chat-suggestions")).getAllByRole("button");
    expect(pills).toHaveLength(3);
    fireEvent.click(pills[1]);
    expect(onPick).toHaveBeenCalledWith(CHAT_EXAMPLES[1]);
  });
});
