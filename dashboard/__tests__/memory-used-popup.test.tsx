/**
 * Next leaps 15-a (master plan R2.7, decision D-15.1): the "From memory"
 * pop-up.
 *
 * - The reply carries `used` (what Iron Jarvis was GIVEN from memory). The
 *   hook whitelists it, both lanes store it on the message, and a quiet line
 *   "From memory: N" sits in the reply's action row beside the receipt.
 * - The pop-up appears by itself only for the newest reply, only while the
 *   chat is on screen, only when memory shaped the reply (`shaped`), never
 *   twice for the same items in one chat, and never when Settings says "Only
 *   when I press". It fades after about eight seconds (a fake clock here),
 *   stays while hovered, focused or while a correction is typed, Escape
 *   closes it, the line brings it back, and it never takes focus from the
 *   composer.
 * - That's wrong on a kept preference calls the EXISTING routes, then says
 *   "Fixed." with Undo (the journal's own `POST /undo/{id}`).
 */

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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      patches: [] as { path: string; body: Record<string, unknown> }[],
      dels: [] as string[],
      gets: [] as string[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      /** What the NEXT replies carry as `used` (one entry per reply, in order). */
      used: [] as unknown[],
      /** ... and as `usedMore` (the hook's name for the done frame's `used_more`). */
      more: [] as unknown[],
      absent: false,
      /** Runs while the turn is under way (before its reply). */
      during: null as null | (() => void),
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    const r = H.api.getResponses[path];
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return typeof r === "function" ? (r as () => unknown)() : r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async (path: string, body: Record<string, unknown>) => {
    H.api.patches.push({ path, body });
    const r = H.api.postResponses[`PATCH ${path}`];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  del: async (path: string) => {
    H.api.dels.push(path);
    const r = H.api.postResponses[`DELETE ${path}`];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
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
      if (H.stream.absent) throw new H.FakeStreamError("no stream", 404);
      await new Promise<void>((r) => setTimeout(r, 0));
      H.stream.during?.();
      const n = H.stream.bodies.length;
      const reply = `Reply ${n}.`;
      onDelta(reply, reply);
      const used = H.stream.used.shift();
      const more = H.stream.more.shift();
      return {
        reply,
        route: { requested: "", provider: "mock", model: "mock", reason: "default" },
        tools_used: [],
        ...(used ? { used } : {}),
        ...(more !== undefined ? { usedMore: more } : {}),
      };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
}));
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
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import ChatPage from "@/app/chat/page";
import {
  FADE_MS,
  MemoryUsed,
  MemoryUsedHidden,
  lineInView,
  ownedElsewhere,
  placeBeside,
} from "@/components/chat/MemoryUsed";
import { TurnReceipt } from "@/components/chat/TurnReceipt";
import {
  SHOW_MS,
  decodeMemoryUsed,
  decodePopupMode,
  decodeUsedMore,
  fixPath,
  lineWords,
  moreWords,
  notListedWords,
  opensByItself,
  popupItems,
  type MemoryUsedItem,
} from "@/lib/memoryUsed";

// ---------------------------------------------------------------- fixtures

const PREF = {
  kind: "preference",
  id: "lesson_ab12",
  label: "Always give prices in euros",
  when: "2026-03-12T10:00:00+00:00",
  href: "/memory#prefs-kept",
  fix: ["correct", "forget"],
  shaped: true,
};
const WORK = {
  kind: "work",
  id: "chat:t7",
  label: "March reconciliation",
  when: "",
  href: "/chat?thread=t7",
  fix: [],
  shaped: false,
};
const items = (...raw: unknown[]): MemoryUsedItem[] => decodeMemoryUsed(raw);

const CHAT_GETS = (mode: unknown = "auto") => ({
  "/models": { models: [] },
  "/chat/personas": { personas: [] },
  "/chat/threads": { threads: [] },
  "/settings": { settings: { memory_used_popup: mode } },
  "/projects": { projects: [] },
  "/agents/mentionable": { agents: [] },
  "/skills": { skills: [] },
  "/workflows": { workflows: [] },
  "/tools": { tools: [] },
  "/undo?session_id=chat": { actions: [] },
  "/chat/approvals/pending": { approvals: [] },
});

/** jsdom lays nothing out (every box is 0 x 0), and the pop-up opens by
 *  itself only beside a line in view (`lineInView`). So the "From memory"
 *  line is given a box: by default one in the middle of the window; a test
 *  moves it (`lineBox.top`) to put it out of view. Every other element keeps
 *  jsdom's own answer. */
const realRect = Element.prototype.getBoundingClientRect;
const lineBox = { left: 40, top: 400, width: 115, height: 24 };
function rectOf(b: { left: number; top: number; width: number; height: number }): DOMRect {
  return {
    ...b,
    x: b.left,
    y: b.top,
    right: b.left + b.width,
    bottom: b.top + b.height,
    toJSON: () => ({}),
  } as DOMRect;
}

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.api.patches.length = 0;
  H.api.dels.length = 0;
  H.api.gets.length = 0;
  H.stream.bodies.length = 0;
  H.stream.used = [];
  H.stream.more = [];
  H.stream.absent = false;
  H.stream.during = null;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = CHAT_GETS();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024 });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: 768 });
  Object.assign(lineBox, { left: 40, top: 400, width: 115, height: 24 });
  Element.prototype.getBoundingClientRect = function (this: Element) {
    return this.matches('[data-testid="memory-used-line"]') ? rectOf(lineBox) : realRect.call(this);
  };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  Element.prototype.getBoundingClientRect = realRect;
});

// ---------------------------------------------------------------- the rules

describe("the rules (lib/memoryUsed)", () => {
  it("decodes only valid items, one per item, and keeps the fixes it allows", () => {
    const got = decodeMemoryUsed([
      PREF,
      PREF,
      { ...WORK, href: "https://elsewhere.example/x" },
      { kind: "secret", id: "x", label: "y" },
      { kind: "note", id: "", label: "no id" },
      "junk",
    ]);
    expect(got.map((g) => g.id)).toEqual(["lesson_ab12", "chat:t7"]);
    expect(got[0].fix).toEqual(["correct", "forget"]);
    expect(got[1].href).toBe("");
    // A client's preference with no client is listed only.
    const cp = decodeMemoryUsed([{ kind: "client_preference", id: "cpref_000000000001", label: "Cash basis", fix: ["correct"] }]);
    expect(cp[0].fix).toEqual([]);
    expect(decodeMemoryUsed(null)).toEqual([]);
  });

  it("opens by itself only for the newest reply, only when memory shaped it, never twice for the same items", () => {
    const shapedReply = { role: "assistant", used: [PREF] };
    expect(opensByItself([{ role: "user" }, shapedReply], "auto")).toBe(true);
    expect(opensByItself([{ role: "user" }, shapedReply], "press")).toBe(false);
    expect(opensByItself([{ role: "user" }, { role: "assistant", used: [WORK] }], "auto")).toBe(false);
    expect(opensByItself([{ role: "user" }, { role: "assistant", used: [] }], "auto")).toBe(false);
    expect(opensByItself([shapedReply, { role: "user" }], "auto")).toBe(false);
    // The same item was shown earlier in this chat (by itself or by a press).
    const shownBefore = { role: "assistant", used: [PREF], usedShown: true };
    expect(opensByItself([shownBefore, { role: "user" }, shapedReply], "auto")).toBe(false);
    // ... unless a NEW item shaped this reply.
    const fresh = { ...PREF, id: "lesson_new1" };
    expect(opensByItself([shownBefore, { role: "user" }, { role: "assistant", used: [PREF, fresh] }], "auto")).toBe(true);
    // Shown this visit but not yet on any message.
    expect(opensByItself([{ role: "user" }, shapedReply], "auto", new Set(["preference:lesson_ab12"]))).toBe(false);
  });

  it("what a reply was given past the cap is counted, and said in plain words", () => {
    expect(decodeUsedMore(4)).toBe(4);
    for (const bad of [0, -2, 1.5, "4", null, undefined, true]) expect(decodeUsedMore(bad)).toBe(0);
    expect(notListedWords(4)).toBe("and 4 more not listed here");
    expect(moreWords(19)).toBe("and 19 more in the reply's details");
    expect(moreWords(19, 4)).toBe("and 19 more in the reply's details, 4 more not listed");
  });

  it("the setting reads 'press' or the default", () => {
    expect(decodePopupMode("press")).toBe("press");
    expect(decodePopupMode("auto")).toBe("auto");
    expect(decodePopupMode(undefined)).toBe("auto");
  });

  it("lists what shaped the reply first, at most five, and fixes through the stores' own routes", () => {
    const many = Array.from({ length: 8 }, (_, i) => ({ ...WORK, id: `w${i}` }));
    const listed = popupItems(items(...many, PREF));
    expect(listed).toHaveLength(5);
    expect(listed[0].id).toBe("lesson_ab12");
    expect(fixPath(items(PREF)[0])).toBe("/memory/preferences/lesson_ab12");
    const cp = items({ kind: "client_preference", id: "cpref_000000000001", label: "Cash basis", fix: ["correct"], client: "client_0123456789ab" });
    expect(fixPath(cp[0])).toBe("/clients/client_0123456789ab/preferences/cpref_000000000001");
    expect(lineWords(3)).toBe("From memory: 3");
  });
});

// ---------------------------------------------------------------- the pop-up

function renderPopup(over: Partial<Parameters<typeof MemoryUsed>[0]> = {}) {
  const shown = vi.fn();
  const closed = vi.fn();
  const out = render(
    <div>
      <textarea data-testid="composer" />
      <MemoryUsed items={items(PREF, WORK)} onShown={shown} onClosed={closed} {...over} />
    </div>,
  );
  return { ...out, shown, closed };
}

describe("the pop-up (components/chat/MemoryUsed)", () => {
  it("appears by itself, says 'From memory', and goes after about eight seconds", () => {
    vi.useFakeTimers();
    const { shown, closed } = renderPopup({ autoOpen: true });
    const pop = screen.getByTestId("memory-used-popup");
    expect(pop.getAttribute("role")).toBe("status");
    expect(pop.textContent).toContain("From memory");
    expect(pop.textContent).not.toMatch(/\bI used\b/);
    expect(within(pop).getAllByTestId("memory-used-item").map((li) => li.textContent)).toEqual([
      expect.stringContaining("Your preference: Always give prices in euros"),
      expect.stringContaining("Earlier work: March reconciliation"),
    ]);
    expect(shown).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(SHOW_MS - FADE_MS - 10);
    });
    expect(screen.getByTestId("memory-used-popup").getAttribute("data-leaving")).toBe("false");
    act(() => {
      vi.advanceTimersByTime(20);
    });
    expect(screen.getByTestId("memory-used-popup").getAttribute("data-leaving")).toBe("true");
    act(() => {
      vi.advanceTimersByTime(FADE_MS);
    });
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    expect(closed).toHaveBeenCalledTimes(1);
  });

  it("does not appear by itself when the page did not say so, and the line brings it back", () => {
    vi.useFakeTimers();
    const { shown } = renderPopup();
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    const line = screen.getByTestId("memory-used-line");
    expect(line.textContent).toBe("From memory: 2");
    fireEvent.click(line);
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    expect(line.getAttribute("aria-expanded")).toBe("true");
    expect(shown).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(SHOW_MS);
    });
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    fireEvent.click(line);
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
  });

  it("stays while hovered, and runs the rest of its time once the pointer leaves", () => {
    vi.useFakeTimers();
    renderPopup({ autoOpen: true });
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    fireEvent.mouseEnter(screen.getByTestId("memory-used-popup"));
    act(() => {
      vi.advanceTimersByTime(SHOW_MS * 3);
    });
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    fireEvent.mouseLeave(screen.getByTestId("memory-used-popup"));
    act(() => {
      vi.advanceTimersByTime(SHOW_MS - 3000 - 100);
    });
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
  });

  it("stays while the keyboard focus is inside it", () => {
    vi.useFakeTimers();
    renderPopup({ autoOpen: true });
    const close = screen.getByTestId("memory-used-close");
    act(() => {
      close.focus();
    });
    act(() => {
      vi.advanceTimersByTime(SHOW_MS * 2);
    });
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    act(() => {
      (screen.getByTestId("composer") as HTMLTextAreaElement).focus();
    });
    act(() => {
      vi.advanceTimersByTime(SHOW_MS);
    });
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
  });

  it("stays while a correction is typed, even with the pointer and focus elsewhere", () => {
    vi.useFakeTimers();
    renderPopup({ autoOpen: true });
    fireEvent.click(screen.getByTestId("memory-used-wrong"));
    fireEvent.change(screen.getByTestId("memory-used-right"), { target: { value: "Prices in pounds" } });
    act(() => {
      (screen.getByTestId("composer") as HTMLTextAreaElement).focus();
    });
    act(() => {
      vi.advanceTimersByTime(SHOW_MS * 3);
    });
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    fireEvent.change(screen.getByTestId("memory-used-right"), { target: { value: "" } });
    act(() => {
      vi.advanceTimersByTime(SHOW_MS);
    });
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
  });

  it("never moves the focus from the composer, and Escape closes it only", () => {
    vi.useFakeTimers();
    const composer = render(<textarea data-testid="box" />).getByTestId("box") as HTMLTextAreaElement;
    composer.focus();
    const other = vi.fn();
    window.addEventListener("keydown", other);
    render(<MemoryUsed items={items(PREF)} autoOpen />);
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    expect(document.activeElement).toBe(composer);
    fireEvent.keyDown(composer, { key: "Escape" });
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    expect(other).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(composer);
    window.removeEventListener("keydown", other);
  });

  it("leaves Escape to another dialog or menu that has the keyboard", () => {
    vi.useFakeTimers();
    render(
      <div>
        <div role="dialog" aria-label="Palette">
          <input data-testid="palette-box" />
        </div>
        <textarea data-testid="composer" />
      </div>,
    );
    render(<MemoryUsed items={items(PREF)} autoOpen />);
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    const palette = vi.fn();
    window.addEventListener("keydown", palette);
    fireEvent.keyDown(screen.getByTestId("palette-box"), { key: "Escape" });
    expect(palette).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    // The composer's own Escape (no menu open) still closes the box only.
    fireEvent.keyDown(screen.getByTestId("composer"), { key: "Escape" });
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    expect(palette).toHaveBeenCalledTimes(1);
    window.removeEventListener("keydown", palette);
    // The box's own line (aria-expanded while open) is never "elsewhere".
    const line = screen.getByTestId("memory-used-line");
    expect(ownedElsewhere(line, null, line)).toBe(false);
    expect(ownedElsewhere(screen.getByTestId("palette-box"), null, line)).toBe(true);
  });

  it("counts what the reply was given past the cap, on the line and in the box", () => {
    vi.useFakeTimers();
    const { unmount } = render(<MemoryUsed items={items(PREF, WORK)} more={4} />);
    const line = screen.getByTestId("memory-used-line");
    expect(line.textContent).toBe("From memory: 6");
    fireEvent.click(line);
    expect(screen.getByTestId("memory-used-more").textContent).toBe("and 4 more not listed here");
    unmount();
    const seven = Array.from({ length: 7 }, (_, i) => ({ ...WORK, id: `w${i}` }));
    render(<MemoryUsed items={items(...seven)} more={3} />);
    fireEvent.click(screen.getByTestId("memory-used-line"));
    expect(screen.getByTestId("memory-used-more").textContent).toBe(
      "and 2 more in the reply's details, 3 more not listed",
    );
  });

  it("closes when a turn starts, and leaves Escape to the composer's Stop while it runs", () => {
    vi.useFakeTimers();
    const { rerender } = render(<MemoryUsed items={items(PREF)} autoOpen />);
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    rerender(<MemoryUsed items={items(PREF)} autoOpen busy />);
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    // Opened by a press during the turn: Escape is not caught here.
    fireEvent.click(screen.getByTestId("memory-used-line"));
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    const stop = vi.fn();
    window.addEventListener("keydown", stop);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(stop).toHaveBeenCalledTimes(1);
    window.removeEventListener("keydown", stop);
  });

  it("behind the Code view (the chat hidden) it never appears by itself, and an open one closes", () => {
    vi.useFakeTimers();
    const shown = vi.fn();
    const { rerender } = render(
      <MemoryUsedHidden.Provider value={true}>
        <MemoryUsed items={items(PREF)} autoOpen onShown={shown} />
      </MemoryUsedHidden.Provider>,
    );
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    // Never shown, so never marked shown: its items may still open a later reply's.
    expect(shown).not.toHaveBeenCalled();
    // Back in front: the moment has passed, so it stays quiet; the line opens it.
    rerender(
      <MemoryUsedHidden.Provider value={false}>
        <MemoryUsed items={items(PREF)} autoOpen onShown={shown} />
      </MemoryUsedHidden.Provider>,
    );
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    expect(shown).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("memory-used-line"));
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
    expect(shown).toHaveBeenCalledTimes(1);
    // The Code view comes to the front while it is open: it goes.
    rerender(
      <MemoryUsedHidden.Provider value={true}>
        <MemoryUsed items={items(PREF)} autoOpen onShown={shown} />
      </MemoryUsedHidden.Provider>,
    );
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
  });

  it("That's wrong: the right preference is saved through PATCH, then Fixed with Undo", async () => {
    H.api.postResponses["PATCH /memory/preferences/lesson_ab12"] = { preference: { id: "lesson_ab12" }, action_id: "tool_1" };
    H.api.postResponses["/undo/tool_1"] = { ok: true };
    render(<MemoryUsed items={items(PREF)} autoOpen />);
    fireEvent.click(screen.getByTestId("memory-used-wrong"));
    fireEvent.change(screen.getByTestId("memory-used-right"), { target: { value: "Prices in pounds" } });
    fireEvent.click(screen.getByTestId("memory-used-save"));
    const done = await screen.findByTestId("memory-used-done");
    expect(H.api.patches).toEqual([{ path: "/memory/preferences/lesson_ab12", body: { text: "Prices in pounds" } }]);
    expect(done.getAttribute("data-state")).toBe("fixed");
    expect(done.textContent).toContain("Fixed.");
    fireEvent.click(screen.getByTestId("memory-used-undo"));
    await waitFor(() => expect(screen.getByTestId("memory-used-done").getAttribute("data-state")).toBe("undone"));
    expect(H.api.posts.map((p) => p.path)).toEqual(["/undo/tool_1"]);
  });

  it("Forget it deletes through the store's own route; no Undo is promised when none was recorded", async () => {
    H.api.postResponses["DELETE /memory/preferences/lesson_ab12"] = { deleted: "lesson_ab12" };
    render(<MemoryUsed items={items(PREF)} autoOpen />);
    fireEvent.click(screen.getByTestId("memory-used-wrong"));
    fireEvent.click(screen.getByTestId("memory-used-forget"));
    const done = await screen.findByTestId("memory-used-done");
    expect(H.api.dels).toEqual(["/memory/preferences/lesson_ab12"]);
    expect(done.getAttribute("data-state")).toBe("forgotten");
    expect(screen.queryByTestId("memory-used-undo")).toBeNull();
    expect(done.textContent).toContain("can't be undone");
  });

  it("an earlier message offers no fix, and an item with no fix shows no That's wrong", () => {
    render(<MemoryUsed items={items(PREF, WORK)} autoOpen readOnly />);
    expect(screen.queryByTestId("memory-used-wrong")).toBeNull();
    cleanup();
    render(<MemoryUsed items={items(WORK)} autoOpen />);
    expect(screen.queryByTestId("memory-used-wrong")).toBeNull();
  });

  it("sits inside the window: at most 340 px wide and 16 px from each edge, at 390 px and on a desk", () => {
    const at = (left: number, top: number) =>
      ({ getBoundingClientRect: () => ({ left, top, bottom: top + 24, right: left + 90, width: 90, height: 24 }) }) as unknown as HTMLElement;
    const size = (w: number, h: number) => {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
      Object.defineProperty(window, "innerHeight", { configurable: true, value: h });
    };
    size(390, 844);
    expect(placeBeside(at(300, 400))).toEqual({ left: 34, width: 340, top: 430, maxHeight: 392 });
    // Near the bottom of the screen it opens above its line.
    expect(placeBeside(at(40, 780))).toEqual({ left: 34, width: 340, bottom: 70, maxHeight: 758 });
    // A narrower phone: the window's width less 16 px each side.
    size(320, 640);
    expect(placeBeside(at(200, 100))).toEqual({ left: 16, width: 288, top: 130, maxHeight: 488 });
    size(1280, 900);
    expect(placeBeside(at(900, 300))).toEqual({ left: 900, width: 340, top: 330, maxHeight: 548 });
    expect(placeBeside(at(1200, 300))).toEqual({ left: 924, width: 340, top: 330, maxHeight: 548 });
  });

  it("never covers the message box: above its line when a short list does not fit over the box", () => {
    // What a real browser showed at 390 px: the line at y 580, the message
    // box's card from y 700. Below the line there were 86 px before the box,
    // and the old placement opened there and covered the box.
    const at = (left: number, top: number) =>
      ({ getBoundingClientRect: () => ({ left, top, bottom: top + 24, right: left + 115, width: 115, height: 24 }) }) as unknown as HTMLElement;
    const composer = document.createElement("div");
    composer.setAttribute("data-testid", "chat-composer");
    let card = { top: 700, height: 140 };
    composer.getBoundingClientRect = () =>
      ({ left: 24, top: card.top, right: 366, bottom: card.top + card.height, width: 342, height: card.height }) as DOMRect;
    document.body.appendChild(composer);
    try {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
      Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });
      const phone = placeBeside(at(22, 580))!;
      expect(phone).toEqual({ left: 22, width: 340, bottom: 270, maxHeight: 558 });
      expect(844 - phone.bottom!).toBeLessThan(card.top); // its lower edge is above the box
      // On a desk: below the line, but never reaching the box (it scrolls).
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
      Object.defineProperty(window, "innerHeight", { configurable: true, value: 900 });
      card = { top: 730, height: 44 };
      const desk = placeBeside(at(619, 404))!;
      expect(desk).toEqual({ left: 619, width: 340, top: 434, maxHeight: 290 });
      expect(desk.top! + desk.maxHeight!).toBeLessThan(card.top);
      // A hidden message box (the dock has it) is not in the way.
      card = { top: 730, height: 0 };
      expect(placeBeside(at(619, 404))).toEqual({ left: 619, width: 340, top: 434, maxHeight: 444 });
      // The box itself is held to that room (it scrolls inside it).
      render(<MemoryUsed items={items(PREF)} autoOpen />);
      expect(screen.getByTestId("memory-used-popup").style.maxHeight).toMatch(/^min\(60vh, \d+px\)$/);
    } finally {
      composer.remove();
    }
  });

  it("a line out of view (the reader scrolled up) never opens it by itself, and a press never puts it over the message box", () => {
    // The review's case at 390 x 844: the message box from y 688, the newest
    // reply's line below the window (y 1000) because the reader scrolled up
    // to earlier messages. The old placement pinned the box to bottom: 16px,
    // over the message box.
    const composer = document.createElement("div");
    composer.setAttribute("data-testid", "chat-composer");
    composer.getBoundingClientRect = () => rectOf({ left: 24, top: 688, width: 342, height: 156 });
    document.body.appendChild(composer);
    try {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
      Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });
      const at = (top: number) =>
        ({ getBoundingClientRect: () => rectOf({ left: 22, top, width: 115, height: 24 }) }) as unknown as HTMLElement;
      // Placed above the message box, never over it.
      const off = placeBeside(at(1000))!;
      expect(off).toEqual({ left: 22, width: 340, bottom: 162, maxHeight: 666 });
      expect(844 - off.bottom!).toBeLessThan(688);
      // In view: above the message box and inside the window. Out of view:
      // under the message box, past the window's edge, or not drawn.
      expect(lineInView(at(580))).toBe(true);
      expect(lineInView(at(1000))).toBe(false);
      expect(lineInView(at(680))).toBe(false); // reaches under the message box
      expect(lineInView(at(-30))).toBe(false); // scrolled past the top
      expect(lineInView({ getBoundingClientRect: () => rectOf({ left: 0, top: 0, width: 0, height: 0 }) } as unknown as HTMLElement)).toBe(false);
      // The page said "by itself", but the line is out of view: it stays shut.
      lineBox.top = 1000;
      const shown = vi.fn();
      render(<MemoryUsed items={items(PREF)} autoOpen onShown={shown} />);
      expect(screen.queryByTestId("memory-used-popup")).toBeNull();
      expect(shown).not.toHaveBeenCalled();
      // A press opens it, above the message box.
      fireEvent.click(screen.getByTestId("memory-used-line"));
      const pop = screen.getByTestId("memory-used-popup");
      expect(pop.style.bottom).toBe("162px");
      expect(pop.style.maxHeight).toBe("min(60vh, 666px)");
      expect(shown).toHaveBeenCalledTimes(1);
    } finally {
      composer.remove();
    }
  });

  it("inside the chat's list: a line scrolled out of the list is out of view", () => {
    const list = document.createElement("div");
    list.setAttribute("data-testid", "chat-scroll");
    list.getBoundingClientRect = () => rectOf({ left: 0, top: 60, width: 1024, height: 500 });
    const line = document.createElement("button");
    list.appendChild(line);
    document.body.appendChild(list);
    try {
      let top = 300;
      line.getBoundingClientRect = () => rectOf({ left: 40, top, width: 115, height: 24 });
      expect(lineInView(line)).toBe(true);
      top = 40; // above the list's top (under the page's header)
      expect(lineInView(line)).toBe(false);
      top = 545; // past the list's bottom
      expect(lineInView(line)).toBe(false);
    } finally {
      list.remove();
    }
  });

  it("the reply's details list every item under 'From memory'", () => {
    render(
      <TurnReceipt
        inline
        route={{ requested: "", provider: "mock", model: "mock", reason: "default" }}
        memoryUsed={items(PREF, WORK)}
      />,
    );
    fireEvent.click(screen.getByTestId("turn-receipt"));
    const detail = screen.getByTestId("turn-memory-used");
    expect(detail.textContent).toContain("From memory");
    expect(within(detail).getAllByTestId("turn-memory-used-item").map((d) => d.textContent)).toEqual([
      "Your preference: Always give prices in euros",
      "Earlier work: March reconciliation",
    ]);
    expect(screen.queryByTestId("turn-memory-used-more")).toBeNull();
  });

  it("the reply's details say how many more were given than listed", () => {
    render(
      <TurnReceipt
        inline
        route={{ requested: "", provider: "mock", model: "mock", reason: "default" }}
        memoryUsed={items(PREF, WORK)}
        memoryMore={4}
      />,
    );
    fireEvent.click(screen.getByTestId("turn-receipt"));
    expect(screen.getByTestId("turn-memory-used-more").textContent).toBe("and 4 more not listed here");
  });
});

// ---------------------------------------------------------------- the page

async function send(text: string, reply: string) {
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await screen.findByText(reply);
  return box;
}

const threadSaves = () => H.api.puts.filter((p) => p.path.startsWith("/chat/threads/"));
const lastSaved = () =>
  threadSaves().at(-1)!.body.messages as {
    role: string;
    content: string;
    used?: unknown;
    usedShown?: boolean;
    usedMore?: unknown;
  }[];
const settingsReads = () => H.api.gets.filter((g) => g === "/settings").length;
/** The reply's end is SAVED: the page queues the save and, in the same
 *  synchronous step, decides about the pop-up (maybePopMemory). Once a save
 *  holds `reply`, that decision has been made. */
const savedReply = (reply: string) =>
  threadSaves().some((p) => (p.body.messages as { content?: string }[]).some((m) => m.content === reply));
/** Every promise already queued has settled, inside act so React draws what
 *  it decided. Not a wait on the clock: a 0 ms task runs only after every
 *  queued promise (the mocked GET has no timers), however loaded the PC is. */
async function drainPromises() {
  await act(async () => {
    await new Promise<void>((r) => setTimeout(r, 0));
  });
}
/** The chat page's own settings read on mount has been made. */
async function mounted() {
  await waitFor(() => expect(settingsReads()).toBeGreaterThan(0));
  return settingsReads();
}

describe("the chat page (both lanes)", () => {
  it("stream lane: the newest reply's pop-up appears by itself, its items are saved, the composer keeps focus", async () => {
    H.stream.used = [[PREF, WORK]];
    render(<ChatPage />);
    const box = await send("how much is the pro plan?", "Reply 1.");
    const pop = await screen.findByTestId("memory-used-popup");
    expect(pop.textContent).toContain("Always give prices in euros");
    expect(screen.getByTestId("memory-used-line").textContent).toBe("From memory: 2");
    expect(document.activeElement).toBe(box);
    await waitFor(() => expect(lastSaved().at(-1)!.used).toEqual([PREF, WORK]));
    await waitFor(() => expect(lastSaved().at(-1)!.usedShown).toBe(true));
  });

  it("never twice for the same items in one chat: a second reply with the same items stays quiet", async () => {
    H.stream.used = [[PREF], [PREF]];
    render(<ChatPage />);
    await send("how much is the pro plan?", "Reply 1.");
    await screen.findByTestId("memory-used-popup");
    fireEvent.click(screen.getByTestId("memory-used-close"));
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    const before = settingsReads();
    await send("and the team plan?", "Reply 2.");
    // The decision is made where the reply's end is saved: the same items
    // were shown, so the page does not even read the setting.
    await waitFor(() => expect(savedReply("Reply 2.")).toBe(true));
    expect(settingsReads()).toBe(before);
    await drainPromises();
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    // The line is still there under the newest reply, and brings it back.
    const lines = screen.getAllByTestId("memory-used-line");
    fireEvent.click(lines[lines.length - 1]);
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
  });

  it("only when memory shaped the reply: a list nothing shaped is a quiet line only", async () => {
    H.stream.used = [[WORK]];
    render(<ChatPage />);
    const before = await mounted();
    await send("hello", "Reply 1.");
    await screen.findByTestId("memory-used-line");
    // Nothing shaped it: decided at the reply's end, without reading the setting.
    await waitFor(() => expect(savedReply("Reply 1.")).toBe(true));
    expect(settingsReads()).toBe(before);
    await drainPromises();
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
  });

  it("Settings 'Only when I press': no pop-up by itself; the line still opens it", async () => {
    H.api.getResponses = CHAT_GETS("press");
    H.stream.used = [[PREF]];
    render(<ChatPage />);
    const before = await mounted();
    await send("how much is the pro plan?", "Reply 1.");
    // The setting is read at the reply's end; its answer decides.
    await waitFor(() => expect(settingsReads()).toBe(before + 1));
    await drainPromises();
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    fireEvent.click(screen.getByTestId("memory-used-line"));
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
  });

  it("POST lane (no /chat/stream): the same line and pop-up, from the response's used", async () => {
    H.stream.absent = true;
    H.api.postResponses["/chat"] = {
      reply: "Posted reply.",
      route: { requested: "", provider: "mock", model: "mock", reason: "default" },
      remembered: [],
      used: [PREF],
    };
    render(<ChatPage />);
    await send("how much is the pro plan?", "Posted reply.");
    await screen.findByTestId("memory-used-popup");
    await waitFor(() => expect(lastSaved().at(-1)!.used).toEqual([PREF]));
    expect(lastSaved().at(-1)!.usedMore).toBeUndefined();
  });

  it("stream lane: keeps how many more the reply was given than listed", async () => {
    H.stream.used = [[PREF, WORK]];
    H.stream.more = [5];
    render(<ChatPage />);
    await send("how much is the pro plan?", "Reply 1.");
    expect((await screen.findByTestId("memory-used-line")).textContent).toBe("From memory: 7");
    await waitFor(() => expect(lastSaved().at(-1)!.usedMore).toBe(5));
  });

  it("POST lane: keeps how many more the reply was given than listed", async () => {
    H.stream.absent = true;
    H.api.postResponses["/chat"] = {
      reply: "Posted reply.",
      route: { requested: "", provider: "mock", model: "mock", reason: "default" },
      remembered: [],
      used: [PREF],
      used_more: 3,
    };
    render(<ChatPage />);
    await send("and the team plan?", "Posted reply.");
    expect((await screen.findByTestId("memory-used-line")).textContent).toBe("From memory: 4");
    await waitFor(() => expect(lastSaved().at(-1)!.usedMore).toBe(3));
  });

  it("a reader who scrolled up to earlier messages while the reply ran: no pop-up by itself; the line still opens it", async () => {
    H.stream.used = [[PREF]];
    // While the turn runs, the reader scrolls the chat up, away from its
    // newest message (the page stops following and shows "Jump to latest").
    H.stream.during = () => {
      const list = screen.getByTestId("chat-scroll");
      Object.defineProperty(list, "scrollHeight", { configurable: true, value: 3000 });
      Object.defineProperty(list, "clientHeight", { configurable: true, value: 600 });
      Object.defineProperty(list, "scrollTop", { configurable: true, writable: true, value: 0 });
      fireEvent.scroll(list);
    };
    render(<ChatPage />);
    const before = await mounted();
    await send("how much is the pro plan?", "Reply 1.");
    await screen.findByTestId("memory-used-line");
    // Decided at the reply's end, without reading the setting.
    await waitFor(() => expect(savedReply("Reply 1.")).toBe(true));
    expect(settingsReads()).toBe(before);
    await drainPromises();
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    // The line under the reply still opens it.
    fireEvent.click(screen.getByTestId("memory-used-line"));
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
  });

  it("only while the chat is on screen: a reply that ends in a hidden window stays quiet", async () => {
    H.stream.used = [[PREF]];
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    try {
      render(<ChatPage />);
      const before = await mounted();
      await send("how much is the pro plan?", "Reply 1.");
      await screen.findByTestId("memory-used-line");
      // Off screen: decided at the reply's end, without reading the setting.
      await waitFor(() => expect(savedReply("Reply 1.")).toBe(true));
      expect(settingsReads()).toBe(before);
      await drainPromises();
      expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    } finally {
      delete (document as unknown as { visibilityState?: unknown }).visibilityState;
    }
  });

  it("the stream hook whitelists the done frame's used", async () => {
    const real = await vi.importActual<typeof import("@/lib/useChatStream")>("@/lib/useChatStream");
    const ev = real.decodeSSE("done", JSON.stringify({ reply: "x", used: [PREF, { kind: "secret", id: "s", label: "no" }] }));
    expect(ev && ev.type === "done" ? ev.used : null).toEqual([PREF]);
    const none = real.decodeSSE("done", JSON.stringify({ reply: "x", used: [], used_more: 3 }));
    expect(none && none.type === "done" ? none.used : "absent").toBeUndefined();
    expect(none && none.type === "done" ? none.used_more : "absent").toBeUndefined();
    const more = real.decodeSSE("done", JSON.stringify({ reply: "x", used: [PREF], used_more: 3 }));
    expect(more && more.type === "done" ? more.used_more : null).toBe(3);
    const zero = real.decodeSSE("done", JSON.stringify({ reply: "x", used: [PREF], used_more: 0 }));
    expect(zero && zero.type === "done" ? zero.used_more : "absent").toBeUndefined();
  });

  it("a reply with nothing from memory has no line", async () => {
    render(<ChatPage />);
    await send("hello", "Reply 1.");
    expect(screen.queryByTestId("memory-used-line")).toBeNull();
  });
});
