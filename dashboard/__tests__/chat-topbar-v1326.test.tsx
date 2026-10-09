/**
 * CALM CHAT W1-1 (v1.326.0): the chat page's chrome.
 *
 *  - NO CARD: the conversation section (`chat-card`, testid kept) carries no
 *    `card-surface` fill, border or shadow; it sits on the page.
 *  - A SLIM TOP BAR (`chat-topbar`) replaces the card's header row. Left: the
 *    breadcrumb "<project> / <thread title>" (just the title with no project,
 *    nothing on an empty new chat) and, in a project, the Chat/Tasks/Board/
 *    Media tabs as plain text (open = ink, no underline). Right: a quiet
 *    Project button, Share (moved up from the composer footer) and "⋯".
 *  - "⋯" holds hands-free Voice chat, Read replies aloud, the Persona choice
 *    + its editor and the project panel toggle; Escape closes it and puts
 *    focus back on the trigger.
 *  - The project panel is a DRAWER (role dialog "Project panel"), not a
 *    permanent column: closed by default, opened by a press (focus moves in),
 *    closed by Escape or its Close button (focus goes back to the opener).
 *
 * Harness: the v1.315.0 chat mocks (ChatPage rendered for real, an unmocked
 * GET answers {}), with dictation and spoken replies switchable.
 */

import { type ReactNode, useState } from "react";
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
    api: { responses: {} as Record<string, unknown> },
    dict: { supported: false, starts: 0, stops: 0 },
    tts: { supported: false, enabled: false, toggles: 0 },
    stream: { result: { reply: "Noted.", tools_used: [] } as Record<string, unknown> },
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
    if (r === undefined && path.startsWith("/fs/files")) return {};
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
    run: () => Promise.resolve(H.stream.result),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: H.dict.supported,
    reason: null,
    engine: null,
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {
      H.dict.starts += 1;
    },
    stop: () => {
      H.dict.stops += 1;
    },
    reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: H.tts.supported,
    enabled: H.tts.enabled,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {
      H.tts.toggles += 1;
    },
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
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
import { ProjectDrawer } from "@/components/chat/ProjectDrawer";

const PROJECT_KEY = "ij_chat_project";
const PROJECT = { id: "p1", name: "Q3 Bookkeeping" };
const AT = "2026-10-01T10:00:00Z";
const QUICK = { id: "tq", title: "Quick question", updated_at: AT, messages: 2 };
const LEDGER = { id: "tp", title: "Ledger totals", project_id: "p1", updated_at: AT, messages: 2 };
const TQ = {
  id: "tq",
  title: "Quick question",
  updated_at: AT,
  messages: [
    { role: "user", content: "what is due friday" },
    { role: "assistant", content: "The payroll filing is due Friday." },
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

function classes(el: Element | null | undefined): string[] {
  return ((el?.getAttribute("class") ?? "") as string).split(/\s+/).filter(Boolean);
}

const bar = () => screen.findByTestId("chat-topbar");

async function openMenu(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: "More chat options" }));
  return await screen.findByRole("group", { name: "Chat options" });
}

beforeEach(() => {
  H.dict.supported = false;
  H.dict.starts = 0;
  H.dict.stops = 0;
  H.tts.supported = false;
  H.tts.enabled = false;
  H.tts.toggles = 0;
  H.stream.result = { reply: "Noted.", tools_used: [] };
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [QUICK, LEDGER] },
    "/chat/threads?project_id=p1": { threads: [LEDGER] },
    "/chat/threads/tq": TQ,
    "/chat/threads/tp": TP,
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
  delete (window as unknown as { matchMedia?: unknown }).matchMedia;
});

/** A matchMedia that answers min/max-width queries for `width`. */
function installViewport(width: number) {
  (window as unknown as { matchMedia: (q: string) => MediaQueryList }).matchMedia = (q: string) => {
    const min = /min-width:\s*(\d+(?:\.\d+)?)px/.exec(q);
    const max = /max-width:\s*(\d+(?:\.\d+)?)px/.exec(q);
    const matches = min ? width >= Number(min[1]) : max ? width <= Number(max[1]) : false;
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

/** Send one message whose reply MADE a file (the turn's `documents`). */
async function sendTurnThatMadeAFile(): Promise<HTMLTextAreaElement> {
  H.stream.result = { reply: "Saved the report.", tools_used: [], documents: ["C:/work/report.docx"] };
  const box = (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
  box.focus();
  fireEvent.change(box, { target: { value: "write the report" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await screen.findByText("Saved the report.");
  return box;
}

describe("no card around the conversation", () => {
  it("the chat section keeps its testid and carries no card fill, border or shadow", async () => {
    render(<ChatPage />);
    const card = await screen.findByTestId("chat-card");
    const c = classes(card);
    expect(c).not.toContain("card-surface");
    expect(c.some((x) => /^(border|shadow|bg-)/.test(x))).toBe(false);
    // CONTROL: still the flex column the transcript and composer divide.
    expect(c).toEqual(expect.arrayContaining(["flex", "h-full", "min-h-0", "flex-col"]));
  });
});

describe("the top bar", () => {
  it("sits above the conversation, slim and borderless, and holds Share (no longer under the composer)", async () => {
    render(<ChatPage />);
    const top = await bar();
    const card = await screen.findByTestId("chat-card");
    expect(top.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(card.contains(top)).toBe(false);
    expect(classes(top)).toContain("sm:h-12");
    expect(classes(top).some((x) => /^border/.test(x))).toBe(false);
    const shares = screen.getAllByRole("button", { name: "Share this chat" });
    expect(shares).toHaveLength(1);
    expect(top.contains(shares[0])).toBe(true);
    // No thread yet: disabled, and the title says why.
    expect((shares[0] as HTMLButtonElement).disabled).toBe(true);
    expect(shares[0].getAttribute("title")).toMatch(/shared after its first reply/);
  });

  it("an empty new chat shows no breadcrumb", async () => {
    render(<ChatPage />);
    await bar();
    await screen.findByRole("textbox", { name: "Message" });
    expect(screen.queryByTestId("chat-breadcrumb")).toBeNull();
  });

  it("a saved chat with no project shows just its title; Share is enabled", async () => {
    window.history.replaceState({}, "", "/chat?thread=tq");
    render(<ChatPage />);
    await screen.findByText("The payroll filing is due Friday.");
    const crumb = await within(await bar()).findByTestId("chat-breadcrumb");
    await waitFor(() => expect(crumb.textContent).toBe("Quick question"));
    expect(crumb.textContent).not.toContain("/");
    await waitFor(() =>
      expect((screen.getByRole("button", { name: "Share this chat" }) as HTMLButtonElement).disabled).toBe(false),
    );
  });

  it("a project chat reads '<project> / <title>' and shows its views as plain text tabs", async () => {
    window.localStorage.setItem(PROJECT_KEY, PROJECT.id);
    window.history.replaceState({}, "", "/chat?thread=tp");
    render(<ChatPage />);
    await screen.findByText("Q3 ledger sums to 88k.");
    const top = await bar();
    const crumb = await within(top).findByTestId("chat-breadcrumb");
    // The separator is its own span (spaced by the row's gap, not by text).
    await waitFor(() =>
      expect(
        Array.from(crumb.children)
          .map((n) => (n.textContent ?? "").trim())
          .join(" "),
      ).toBe("Q3 Bookkeeping / Ledger totals"),
    );
    const list = within(top).getByRole("tablist", { name: "Project views" });
    const tabs = within(list).getAllByRole("tab");
    expect(tabs.map((t) => (t.textContent ?? "").trim().toLowerCase())).toEqual(["chat", "tasks", "board", "media"]);
    // Plain text: the open one in ink, the rest muted, no underline.
    expect(classes(tabs[0])).toContain("text-zinc-100");
    expect(classes(tabs[1])).toContain("text-zinc-500");
    for (const t of tabs) expect(classes(t).some((x) => x.startsWith("border"))).toBe(false);
    // One tablist on screen whatever is showing: Tasks keeps the same bar.
    fireEvent.click(tabs[1]);
    expect(await screen.findByText("surface:tasks")).toBeTruthy();
    expect(screen.getAllByRole("tablist", { name: "Project views" })).toHaveLength(1);
    expect(top.isConnected).toBe(true);
    expect(within(top).getByRole("button", { name: "Share this chat" })).toBeTruthy();
  });
});

describe("the ⋯ menu holds the old header's controls", () => {
  it("is closed until pressed; then holds Voice chat, Read replies aloud, Persona, its editor and the project panel", async () => {
    H.tts.supported = true;
    render(<ChatPage />);
    const trigger = await screen.findByRole("button", { name: "More chat options" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("group", { name: "Chat options" })).toBeNull();
    const panel = await openMenu();
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(within(panel).getByRole("button", { name: "Voice chat" })).toBeTruthy();
    const aloud = within(panel).getByRole("button", { name: /Read replies aloud/ });
    expect(aloud.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(aloud);
    expect(H.tts.toggles).toBe(1);
    const sel = within(panel).getByRole("combobox", { name: "Persona" }) as HTMLSelectElement;
    expect(Array.from(sel.options).some((o) => o.textContent === "+ New persona…")).toBe(true);
    expect(within(panel).getByRole("button", { name: "Modify persona" })).toBeTruthy();
    expect(within(panel).getByRole("button", { name: "Show project panel" })).toBeTruthy();
  });

  it("Escape closes it and puts focus back on the ⋯ button", async () => {
    render(<ChatPage />);
    const panel = await openMenu();
    // Focus went into the panel when it opened.
    await waitFor(() => expect(panel.contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("group", { name: "Chat options" })).toBeNull());
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "More chat options" }));
  });

  it("Voice chat starts hands-free voice, closes the menu, and the bar offers its off switch", async () => {
    H.dict.supported = true;
    render(<ChatPage />);
    const panel = await openMenu();
    fireEvent.click(within(panel).getByRole("button", { name: "Voice chat" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Chat options" })).toBeNull());
    const off = await within(await bar()).findByRole("button", { name: "End voice chat" });
    await waitFor(() => expect(H.dict.starts).toBeGreaterThan(0));
    fireEvent.click(off);
    await waitFor(() => expect(screen.queryByRole("button", { name: "End voice chat" })).toBeNull());
  });

  it("Modify persona opens the persona editor", async () => {
    render(<ChatPage />);
    const panel = await openMenu();
    fireEvent.click(within(panel).getByRole("button", { name: "Modify persona" }));
    expect(await screen.findByRole("button", { name: "Close persona editor" })).toBeTruthy();
  });
});

describe("the project panel is a drawer on demand", () => {
  it("is closed by default, with no permanent column or strip", async () => {
    render(<ChatPage />);
    await bar();
    await screen.findByRole("textbox", { name: "Message" });
    expect(screen.queryByRole("dialog", { name: "Project panel" })).toBeNull();
    expect(screen.queryByLabelText("Show project panel")).toBeNull(); // the old vertical strip
    expect(screen.queryByRole("combobox", { name: "Project" })).toBeNull();
  });

  it("the Project button opens it with focus inside; Escape closes it and focus returns to the button", async () => {
    window.localStorage.setItem(PROJECT_KEY, PROJECT.id);
    render(<ChatPage />);
    const btn = await within(await bar()).findByRole("button", { name: /^Project: Q3 Bookkeeping/ });
    btn.focus();
    fireEvent.click(btn);
    const drawer = await screen.findByRole("dialog", { name: "Project panel" });
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    // Its contents are the old rail's: the project picker, Files/Knowledge.
    expect(within(drawer).getByRole("combobox", { name: "Project" })).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "knowledge" })).toBeTruthy();
    await waitFor(() => expect(drawer.contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Project panel" })).toBeNull());
    expect(document.activeElement).toBe(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("false");
  });

  it("its Close button closes it; the ⋯ menu's toggle opens it too", async () => {
    render(<ChatPage />);
    const panel = await openMenu();
    fireEvent.click(within(panel).getByRole("button", { name: "Show project panel" }));
    const drawer = await screen.findByRole("dialog", { name: "Project panel" });
    fireEvent.click(within(drawer).getByRole("button", { name: "Close project panel" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Project panel" })).toBeNull());
  });

  it("opened from the ⋯ menu, Escape gives focus back to the ⋯ button, never to the page body", async () => {
    render(<ChatPage />);
    const panel = await openMenu();
    const row = within(panel).getByRole("button", { name: "Show project panel" });
    // A real press focuses the row first; the row then unmounts with the menu.
    row.focus();
    fireEvent.click(row);
    const drawer = await screen.findByRole("dialog", { name: "Project panel" });
    await waitFor(() => expect(drawer.contains(document.activeElement)).toBe(true));
    expect(row.isConnected).toBe(false);
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Project panel" })).toBeNull());
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "More chat options" }));
  });

  it("Escape typed in the composer belongs to the composer, not the drawer", async () => {
    render(<ChatPage />);
    fireEvent.click(await within(await bar()).findByRole("button", { name: /^Project/ }));
    await screen.findByRole("dialog", { name: "Project panel" });
    const box = await screen.findByRole("textbox", { name: "Message" });
    fireEvent.keyDown(box, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "Project panel" })).toBeTruthy();
  });

  it("a reply that made a file opens it beside the chat WITHOUT taking the caret from the composer", async () => {
    render(<ChatPage />);
    const box = await sendTurnThatMadeAFile();
    const drawer = await screen.findByRole("dialog", { name: "Project panel" });
    expect(drawer.contains(document.activeElement)).toBe(false);
    expect(document.activeElement).toBe(box);
  });

  it("on a phone a reply that made a file does NOT cover the chat; a press still opens the drawer", async () => {
    installViewport(390);
    render(<ChatPage />);
    await sendTurnThatMadeAFile();
    // The turn has settled (its reply is on screen) and nothing opened.
    expect(screen.queryByRole("dialog", { name: "Project panel" })).toBeNull();
    fireEvent.click(await within(await bar()).findByRole("button", { name: /^Project/ }));
    expect(await screen.findByRole("dialog", { name: "Project panel" })).toBeTruthy();
  });
});

/** The drawer shell on its own: an opener that may unmount as it opens the
 *  drawer (like the ⋯ row), a named return target, and fields inside. */
function DrawerHarness({ openerUnmounts = false }: { openerUnmounts?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" data-testid="fallback">
        Fallback
      </button>
      {!(openerUnmounts && open) && (
        <button type="button" onClick={() => setOpen(true)}>
          Open drawer
        </button>
      )}
      {open && (
        <ProjectDrawer
          width={320}
          takeFocus
          returnFocusTo={() => document.querySelector<HTMLElement>('[data-testid="fallback"]')}
          onClose={() => setOpen(false)}
        >
          <input aria-label="Rename file" defaultValue="notes.md" />
          <textarea aria-label="Note" defaultValue="" />
          <input type="checkbox" aria-label="Include" />
          <div contentEditable suppressContentEditableWarning aria-label="Rich" role="textbox">
            x
          </div>
        </ProjectDrawer>
      )}
    </div>
  );
}

async function openHarness(): Promise<{ opener: HTMLElement; drawer: HTMLElement }> {
  const opener = screen.getByRole("button", { name: "Open drawer" });
  opener.focus();
  fireEvent.click(opener);
  const drawer = await screen.findByRole("dialog", { name: "Project panel" });
  await waitFor(() => expect(drawer.contains(document.activeElement)).toBe(true));
  return { opener, drawer };
}

describe("the drawer shell (ProjectDrawer)", () => {
  it("Escape in a text field inside it is the field's (cancel an edit): the drawer stays open", async () => {
    render(<DrawerHarness />);
    const { drawer } = await openHarness();
    for (const name of ["Rename file", "Note"]) {
      const field = within(drawer).getByLabelText(name);
      field.focus();
      fireEvent.keyDown(field, { key: "Escape" });
      expect(screen.getByRole("dialog", { name: "Project panel" })).toBeTruthy();
    }
    const rich = within(drawer).getByRole("textbox", { name: "Rich" });
    // jsdom has no isContentEditable; the browser does.
    Object.defineProperty(rich, "isContentEditable", { value: true });
    fireEvent.keyDown(rich, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "Project panel" })).toBeTruthy();
  });

  it("Escape on a checkbox, the Close button or the drawer itself still closes it", async () => {
    render(<DrawerHarness />);
    let { drawer } = await openHarness();
    fireEvent.keyDown(within(drawer).getByRole("checkbox", { name: "Include" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Project panel" })).toBeNull());

    ({ drawer } = await openHarness());
    fireEvent.keyDown(within(drawer).getByRole("button", { name: "Close project panel" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Project panel" })).toBeNull());

    ({ drawer } = await openHarness());
    fireEvent.keyDown(drawer, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Project panel" })).toBeNull());
  });

  it("focus goes back to the opener when it is still there", async () => {
    render(<DrawerHarness />);
    const { opener, drawer } = await openHarness();
    fireEvent.keyDown(drawer, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Project panel" })).toBeNull());
    expect(document.activeElement).toBe(opener);
  });

  it("an opener that unmounted as it opened the drawer: focus goes to the named target, not the body", async () => {
    render(<DrawerHarness openerUnmounts />);
    const { opener, drawer } = await openHarness();
    expect(opener.isConnected).toBe(false);
    fireEvent.keyDown(drawer, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Project panel" })).toBeNull());
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(screen.getByTestId("fallback"));
  });
});
