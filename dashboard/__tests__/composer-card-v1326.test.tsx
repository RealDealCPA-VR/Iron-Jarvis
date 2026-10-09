/**
 * v1.326.0 — Calm chat W1-2: the one-card composer.
 *
 * The composer is the ONLY card on the chat screen and every control lives
 * inside it: the box on top, then one toolbar row (left: "+", the project,
 * approvals, tools, web; right: reasoning, the model, the mic, Send, which is
 * Stop while a reply streams). Chips are ghosts (no fill at rest). The old
 * footer row under the composer is gone; the map, the context gauge and a
 * keyboard hint are quiet text under the card. Notices about the next message
 * sit in a tray ABOVE the card. The transcript fades into the composer area
 * (a mask) instead of a divider line.
 *
 * Harness: the ux-wave3-chat-v1315 mocks (ChatPage rendered for real; an
 * unmocked GET answers {}), plus a stream whose `streaming` flag the test sets.
 * jsdom has no layout, so the look is pinned through classes; the screenshots
 * verify the pixels.
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
    stream: { hold: false, streaming: false, runs: 0 },
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

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: H.stream.streaming,
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
import { stashPageContext } from "@/lib/pageContext";
import {
  COMPOSER_CARD_EDGE,
  composerChipClass,
  composerKeyHint,
  toolsChipWords,
} from "@/lib/composerChips";

/* ------------------------------------------------------------------ data */

const AT = "2026-10-01T10:00:00Z";
const ARMED = {
  id: "ta",
  title: "Armed chat",
  updated_at: AT,
  messages: [
    { role: "user", content: "read the ledger" },
    { role: "assistant", content: "The ledger is read." },
  ],
  setup: { tools: ["read_file"] },
};
const BUILDER = {
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

/** A class that paints a fill AT REST (a hover/focus/variant fill is fine). */
function restingFills(el: Element): string[] {
  return classes(el).filter((c) => /^bg-/.test(c) && c !== "bg-transparent");
}

const box = async () => (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
const card = () => screen.getByTestId("chat-composer");
const toolbar = () => screen.getByTestId("composer-toolbar");

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.stream.hold = false;
  H.stream.streaming = false;
  H.stream.runs = 0;
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/chat/threads/ta": ARMED,
    "/chat/threads/tb": BUILDER,
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
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ---------------------------------------------------------------- pure */

describe("the tools chip's words", () => {
  it("Auto tools is the everyday reading, untinted", () => {
    expect(toolsChipWords(true, 0)).toMatchObject({ text: "Auto tools", armed: 0, on: false });
  });
  it("says so when Auto tools is off and nothing is on", () => {
    expect(toolsChipWords(false, 0)).toMatchObject({ text: "Tools off", armed: 0, on: false });
  });
  it("counts the tools turned on by hand, and tints", () => {
    expect(toolsChipWords(true, 2)).toMatchObject({ text: "Auto tools + 2", armed: 2, on: true });
    expect(toolsChipWords(false, 1)).toMatchObject({ text: "1 tool", armed: 1, on: true });
    expect(toolsChipWords(false, 3).text).toBe("3 tools");
    expect(toolsChipWords(false, 3).title).toMatch(/Auto tools is off/);
  });
  it("never reads a bad count as tools on", () => {
    expect(toolsChipWords(true, -2)).toMatchObject({ text: "Auto tools", armed: 0, on: false });
  });
});

describe("the chip classes and the keyboard line", () => {
  it("a chip is transparent at rest and fills only on hover", () => {
    for (const on of [false, true]) {
      const el = document.createElement("button");
      el.className = composerChipClass(on);
      expect(restingFills(el)).toEqual([]);
      expect(classes(el).some((c) => c.startsWith("hover:bg-"))).toBe(true);
      expect(classes(el)).toContain("h-[30px]");
    }
    expect(composerChipClass(true)).toContain("text-accent-soft");
    expect(composerChipClass(false)).toContain("text-zinc-400");
  });
  it("the card edge is a 0.5px hairline shadow, with a light-theme variant", () => {
    expect(COMPOSER_CARD_EDGE).toContain("shadow-[0_0_0_0.5px_rgb(var(--white)/0.12)");
    expect(COMPOSER_CARD_EDGE).toContain("[[data-scheme=light]_&]:shadow-[0_0_0_0.5px_");
  });
  it("names the keys for the moment: sending, steering, or stopping", () => {
    expect(composerKeyHint(false, true)).toMatch(/^Enter to send/);
    expect(composerKeyHint(true, true)).toMatch(/Ctrl\+Enter sends after this reply/);
    expect(composerKeyHint(true, false)).toBe("Esc stops");
    // Plain words, no em-dash asides.
    for (const s of [composerKeyHint(false, true), composerKeyHint(true, true)]) expect(s).not.toContain("—");
  });
});

/* -------------------------------------------------------------- the page */

/* Calm chat W1-3 (v1.326.0): on a NEW chat the project chip sits just above
 * the card, the conversation map is not drawn and nothing is faded (the
 * composer is centred; new-chat-centre-v1326.test.tsx pins that screen). The
 * pins below are about the card in a CONVERSATION, so they open one first. */
async function openConversation() {
  window.history.replaceState({}, "", "/chat?thread=ta");
  const view = render(<ChatPage />);
  await screen.findByText("The ledger is read.");
  return view;
}

describe("one card holds every control", () => {
  it("the box is on top of the card, and the toolbar under it holds every control", async () => {
    const { container } = await openConversation();
    const b = await box();
    const c = card();
    expect(b.parentElement).toBe(c);
    const t = toolbar();
    expect(c.contains(t)).toBe(true);
    expect(b.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const tb = within(t);
    tb.getByRole("button", { name: "Open the chat menu" });
    tb.getByRole("button", { name: "Switch project" });
    tb.getByRole("button", { name: /^Tools: / });
    tb.getByRole("switch", { name: "Web research" });
    tb.getByTitle("Switch model");
    tb.getByRole("button", { name: "Start dictation" });
    tb.getByRole("button", { name: "Send" });
    // Approvals: v1.327.0 the permission chip replaced the select and kept its
    // id; it sits in the card and names the level (the wire value on data-mode).
    const chip = container.querySelector("#chat-approval-mode") as HTMLButtonElement;
    expect(t.contains(chip)).toBe(true);
    expect(chip.getAttribute("data-testid")).toBe("permission-chip");
    expect(chip.getAttribute("aria-label")).toBe("Permissions: Ask when risky");
    expect(chip.getAttribute("data-mode")).toBe("approve_for_me");
    // Left group then right group: Send and the model sit after the web chip.
    const web = tb.getByRole("switch", { name: "Web research" });
    const send = tb.getByRole("button", { name: "Send" });
    expect(web.compareDocumentPosition(send) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(classes(send)).toContain("ml-auto");
  });

  it("the card is rounded with a hairline shadow: no border, no focus ring, and the box is not a field of its own", async () => {
    render(<ChatPage />);
    const b = await box();
    const c = card();
    expect(classes(c)).toContain("rounded-[24px]");
    expect(classes(c)).toContain("bg-ink-800");
    expect(c.className).toContain("shadow-[0_0_0_0.5px_");
    expect(classes(c).filter((x) => /^border(-|$)/.test(x))).toEqual([]);
    expect(classes(c).filter((x) => /(^|:)ring/.test(x) || x.startsWith("focus-within:"))).toEqual([]);
    expect(classes(b)).not.toContain("field");
    expect(classes(b)).toContain("bg-transparent");
    expect(classes(b)).toContain("outline-none");
    expect(classes(b)).toContain("caret-accent");
    expect(classes(b).filter((x) => /^(border|ring|focus:)/.test(x))).toEqual([]);
  });

  it("the toolbar chips are ghosts at rest; only + carries a soft fill", async () => {
    await openConversation();
    await box();
    const tb = within(toolbar());
    for (const el of [
      tb.getByRole("button", { name: "Switch project" }),
      tb.getByRole("button", { name: /^Tools: / }),
      tb.getByRole("switch", { name: "Web research" }),
      tb.getByTitle("Switch model"),
      tb.getByRole("button", { name: "Start dictation" }),
      document.getElementById("chat-approval-mode") as HTMLElement,
    ]) {
      expect(restingFills(el), el.outerHTML.slice(0, 120)).toEqual([]);
    }
    expect(classes(tb.getByRole("button", { name: "Open the chat menu" }))).toContain("bg-white/[0.07]");
    // Nothing to send: Send is quiet and disabled, not the accent.
    const send = tb.getByRole("button", { name: "Send" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    expect(classes(send)).not.toContain("btn-accent");
    fireEvent.change(await box(), { target: { value: "hello" } });
    await waitFor(() => expect(classes(tb.getByRole("button", { name: "Send" }))).toContain("btn-accent"));
    expect((tb.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("the old footer row is gone; the map, the gauge and the keys are quiet text under the card", async () => {
    await openConversation();
    await box();
    expect(screen.queryByText("Approvals:")).toBeNull();
    const meta = screen.getByTestId("composer-meta");
    expect(card().contains(meta)).toBe(false);
    expect(card().compareDocumentPosition(meta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(meta.contains(screen.getByTestId("open-conversation-map"))).toBe(true);
    const hint = within(meta).getByText(/Enter to send/);
    expect(classes(hint)).toEqual(expect.arrayContaining(["hidden", "sm:inline"]));
  });

  it("no divider line above the composer: the transcript fades into it", async () => {
    await openConversation();
    await box();
    const dock = screen.getByTestId("chat-dock");
    for (const el of [dock, card(), toolbar()]) {
      expect(classes(el).filter((c) => /^border-t/.test(c) || c === "hairline")).toEqual([]);
    }
    const scroller = dock.previousElementSibling as HTMLElement;
    expect(classes(scroller)).toContain("overflow-y-auto");
    expect(scroller.className).toContain("[mask-image:linear-gradient(to_bottom,black_calc(100%-28px),transparent)]");
  });
});

describe("the tools chip", () => {
  it("opens the web and Auto tools switches with their plain lines; turning Auto tools off says so", async () => {
    render(<ChatPage />);
    await box();
    const chip = within(toolbar()).getByRole("button", { name: /^Tools: / });
    expect(chip.textContent).toContain("Auto tools");
    fireEvent.click(chip);
    const menu = await screen.findByTestId("composer-tools-menu");
    expect(within(menu).getByText("Lets this chat search the web and read pages.")).toBeTruthy();
    const auto = within(menu).getByRole("switch", { name: /Auto tools/ });
    expect(auto.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(auto);
    await waitFor(() => expect(chip.textContent).toContain("Tools off"));
    expect(window.localStorage.getItem("ij_chat_auto_tools")).toBe("0");
  });

  it("the web chip is a one-press switch that tints when on", async () => {
    render(<ChatPage />);
    await box();
    const web = within(toolbar()).getByRole("switch", { name: "Web research" });
    expect(web.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(web);
    await waitFor(() => expect(web.getAttribute("aria-checked")).toBe("true"));
    expect(classes(web)).toContain("text-accent-soft");
  });

  it("a tool armed for this chat is counted on the chip and disarmed from its menu", async () => {
    window.history.replaceState({}, "", "/chat?thread=ta");
    render(<ChatPage />);
    await screen.findByText("The ledger is read.");
    const chip = within(toolbar()).getByRole("button", { name: /^Tools: / });
    await waitFor(() => expect(chip.textContent).toContain("Auto tools + 1"));
    expect(classes(chip)).toContain("text-accent-soft");
    fireEvent.click(chip);
    const menu = await screen.findByTestId("composer-tools-menu");
    expect(within(menu).getByText("read_file")).toBeTruthy();
    fireEvent.click(within(menu).getByRole("button", { name: "Disarm read_file" }));
    await waitFor(() => expect(chip.textContent).not.toContain("+ 1"));
    expect(chip.textContent).toContain("Auto tools");
    expect(within(menu).queryByText("read_file")).toBeNull();
  });
});

describe("Send becomes Stop while a reply streams", () => {
  it("one round button in the same spot: Stop while streaming, Send otherwise", async () => {
    H.stream.hold = true;
    H.stream.streaming = true;
    render(<ChatPage />);
    const b = await box();
    expect(within(toolbar()).getByRole("button", { name: "Send" })).toBeTruthy();
    fireEvent.change(b, { target: { value: "hello" } });
    fireEvent.keyDown(b, { key: "Enter" });
    await waitFor(() => expect(H.stream.runs).toBe(1));
    const stop = await within(toolbar()).findByTitle("Stop this turn");
    expect(stop.getAttribute("aria-label")).toBe("Stop");
    expect(classes(stop)).toEqual(expect.arrayContaining(["rounded-full", "bg-accent", "ml-auto"]));
    expect(within(toolbar()).queryByRole("button", { name: "Send" })).toBeNull();
  });
});

describe("notices sit ABOVE the card, attached to it", () => {
  it("a held key's notice is in the tray above the card, never inside it", async () => {
    render(<ChatPage />);
    const b = await box();
    fireEvent.change(b, { target: { value: "my key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG" } });
    fireEvent.keyDown(b, { key: "Enter" });
    const notice = await screen.findByTestId("secret-held");
    const tray = screen.getByTestId("composer-notices");
    expect(tray.contains(notice)).toBe(true);
    expect(card().contains(notice)).toBe(false);
    expect(tray.compareDocumentPosition(card()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Narrower than the card and tucked under its top edge.
    expect(classes(tray)).toEqual(expect.arrayContaining(["mx-4", "-mb-4", "rounded-t-2xl", "empty:hidden"]));
    expect(classes(notice)).not.toContain("border-t");
  });

  it("the agent strip sits in the tray too", async () => {
    window.history.replaceState({}, "", "/chat?thread=tb");
    render(<ChatPage />);
    await screen.findByText("Here it is.");
    const strip = await screen.findByTestId("addressee-strip");
    expect(screen.getByTestId("composer-notices").contains(strip)).toBe(true);
    expect(classes(strip)).not.toContain("border-t");
  });

  it("the page-context chip goes INSIDE the card, above the box", async () => {
    stashPageContext({ title: "Clients", path: "/projects", text: "Acme Corp, 3 open returns" });
    window.history.replaceState({}, "", "/chat?about=page");
    render(<ChatPage />);
    const b = await box();
    const chip = await screen.findByTestId("page-context-chip");
    expect(card().contains(chip)).toBe(true);
    expect(chip.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(restingFills(chip)).toEqual(["bg-white/[0.05]"]);
  });
});

/* The W1-2 review found "Jump to latest" stuck on over a saved chat resting
 * at its bottom: it sat IN the transcript scroller's flow, so showing it added
 * its own height (plus the gap) to the very distance onThreadScroll compares
 * with 80 and it kept itself on; the scroller's `[&>*]:w-full` also stretched
 * it to the column. It now floats from the dock, out of that flow. jsdom has
 * no layout, so this pins the structure (the screenshots verify the pixels). */
describe("Jump to latest floats outside the transcript's flow", () => {
  function scrollTo(scroller: HTMLElement, top: number) {
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 2000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 500 });
    Object.defineProperty(scroller, "scrollTop", { configurable: true, writable: true, value: top });
    fireEvent.scroll(scroller);
  }

  it("shown, the pill adds nothing to the scroller: no new child, the sentinel stays last, and it is not stretched", async () => {
    window.history.replaceState({}, "", "/chat?thread=ta");
    render(<ChatPage />);
    await screen.findByText("The ledger is read.");
    const dock = screen.getByTestId("chat-dock");
    const scroller = dock.previousElementSibling as HTMLElement;
    expect(classes(scroller)).toContain("overflow-y-auto");
    const childrenBefore = scroller.childElementCount;
    const sentinel = scroller.lastElementChild;

    scrollTo(scroller, 0); // 1500px from the bottom: the reader scrolled up
    const pill = await screen.findByRole("button", { name: /Jump to latest/ });

    expect(scroller.contains(pill)).toBe(false);
    expect(scroller.childElementCount).toBe(childrenBefore);
    expect(scroller.lastElementChild).toBe(sentinel);
    expect(dock.contains(pill)).toBe(true);
    expect(classes(pill)).toEqual(expect.arrayContaining(["absolute", "bottom-full", "w-auto"]));
    expect(classes(pill)).not.toContain("w-full");
    expect(classes(dock)).toContain("relative");

    // Back near the bottom (40px off, the resting gap a chart can leave), it goes.
    scrollTo(scroller, 1460);
    await waitFor(() => expect(screen.queryByRole("button", { name: /Jump to latest/ })).toBeNull());
  });
});

/* A saved chat OPENS at its bottom at once: a smooth glide from the top fired
 * scroll events far from the bottom (each releasing the pin) while it was
 * still aimed at a bottom a late chart had already moved. Later turns keep
 * their smooth scroll. */
describe("a saved chat opens at its bottom at once", () => {
  it("the first scroll to the bottom sentinel is instant; a later reply still glides", async () => {
    const calls: { el: Element; behavior: unknown }[] = [];
    Element.prototype.scrollIntoView = function scrollIntoView(this: Element, arg?: unknown) {
      calls.push({ el: this, behavior: (arg as ScrollIntoViewOptions | undefined)?.behavior });
    } as unknown as Element["scrollIntoView"];
    window.history.replaceState({}, "", "/chat?thread=ta");
    render(<ChatPage />);
    await screen.findByText("The ledger is read.");
    const scroller = screen.getByTestId("chat-dock").previousElementSibling as HTMLElement;
    const sentinel = scroller.lastElementChild as Element;
    await waitFor(() => expect(calls.some((c) => c.el === sentinel)).toBe(true));
    expect(calls.find((c) => c.el === sentinel)?.behavior).toBe("auto");

    calls.length = 0;
    const b = await box();
    fireEvent.change(b, { target: { value: "and the totals?" } });
    fireEvent.keyDown(b, { key: "Enter" });
    await screen.findByText("Noted.");
    await waitFor(() => expect(calls.some((c) => c.el === sentinel && c.behavior === "smooth")).toBe(true));
  });
});

describe("the transcript follows content that grows after it opened", () => {
  it("a chart laying out under a chat resting at its bottom keeps it there, with no pill", async () => {
    let fire = () => {};
    class FakeRO {
      constructor(cb: ResizeObserverCallback) {
        fire = () => cb([], this as unknown as ResizeObserver);
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", FakeRO);
    // jsdom has no layout: stand in for the browser. Scrolling the bottom
    // sentinel into view ("block: end") lands at the true bottom, because the
    // sentinel's scroll margin equals the scroller's bottom padding (the
    // round-2 fix: every bottom scroll, the re-pin included, aims there).
    Element.prototype.scrollIntoView = function scrollIntoView(this: Element, arg?: unknown) {
      const sc = this.parentElement as HTMLElement | null;
      if (!sc || this !== sc.lastElementChild) return;
      if ((arg as ScrollIntoViewOptions | undefined)?.block !== "end") return;
      sc.scrollTop = Math.max(0, sc.scrollHeight - sc.clientHeight);
    } as unknown as Element["scrollIntoView"];
    try {
      window.history.replaceState({}, "", "/chat?thread=ta");
      render(<ChatPage />);
      await screen.findByText("The ledger is read.");
      const scroller = screen.getByTestId("chat-dock").previousElementSibling as HTMLElement;
      expect(scroller.lastElementChild?.getAttribute("data-testid")).toBe("chat-bottom");
      const size = (h: number) => {
        Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: h });
        Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 500 });
      };
      size(1000);
      scroller.scrollTop = 468;
      fireEvent.scroll(scroller); // resting at the bottom: still following
      size(1150); // the chart lays out
      fire();
      expect(scroller.scrollTop).toBe(650);
      fireEvent.scroll(scroller);
      await new Promise((r) => setTimeout(r, 50));
      expect(screen.queryByRole("button", { name: /Jump to latest/ })).toBeNull();

      // CONTROL: a reader who scrolled up is never pulled down by growth.
      scroller.scrollTop = 100;
      fireEvent.scroll(scroller);
      await screen.findByRole("button", { name: /Jump to latest/ });
      size(1300);
      fire();
      expect(scroller.scrollTop).toBe(100);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
