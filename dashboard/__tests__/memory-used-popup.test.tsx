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
      absent: false,
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
      const n = H.stream.bodies.length;
      const reply = `Reply ${n}.`;
      onDelta(reply, reply);
      const used = H.stream.used.shift();
      return {
        reply,
        route: { requested: "", provider: "mock", model: "mock", reason: "default" },
        tools_used: [],
        ...(used ? { used } : {}),
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
import { FADE_MS, MemoryUsed, MemoryUsedHidden, placeBeside } from "@/components/chat/MemoryUsed";
import { TurnReceipt } from "@/components/chat/TurnReceipt";
import {
  SHOW_MS,
  decodeMemoryUsed,
  decodePopupMode,
  fixPath,
  lineWords,
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

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.api.patches.length = 0;
  H.api.dels.length = 0;
  H.api.gets.length = 0;
  H.stream.bodies.length = 0;
  H.stream.used = [];
  H.stream.absent = false;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = CHAT_GETS();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
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
    expect(placeBeside(at(300, 400))).toEqual({ left: 34, width: 340, top: 430 });
    // Near the bottom of the screen it opens above its line.
    expect(placeBeside(at(40, 780))).toEqual({ left: 34, width: 340, bottom: 70 });
    // A narrower phone: the window's width less 16 px each side.
    size(320, 640);
    expect(placeBeside(at(200, 100))).toEqual({ left: 16, width: 288, top: 130 });
    size(1280, 900);
    expect(placeBeside(at(900, 300))).toEqual({ left: 900, width: 340, top: 330 });
    expect(placeBeside(at(1200, 300))).toEqual({ left: 924, width: 340, top: 330 });
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
  threadSaves().at(-1)!.body.messages as { role: string; content: string; used?: unknown; usedShown?: boolean }[];

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
    await send("and the team plan?", "Reply 2.");
    // Give the page every chance to decide (its settings read) before asserting.
    await waitFor(() => expect(H.api.gets.filter((g) => g === "/settings").length).toBeGreaterThan(0));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    // The line is still there under the newest reply, and brings it back.
    const lines = screen.getAllByTestId("memory-used-line");
    fireEvent.click(lines[lines.length - 1]);
    expect(screen.getByTestId("memory-used-popup")).toBeTruthy();
  });

  it("only when memory shaped the reply: a list nothing shaped is a quiet line only", async () => {
    H.stream.used = [[WORK]];
    render(<ChatPage />);
    await send("hello", "Reply 1.");
    await screen.findByTestId("memory-used-line");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByTestId("memory-used-popup")).toBeNull();
  });

  it("Settings 'Only when I press': no pop-up by itself; the line still opens it", async () => {
    H.api.getResponses = CHAT_GETS("press");
    H.stream.used = [[PREF]];
    render(<ChatPage />);
    await send("how much is the pro plan?", "Reply 1.");
    await waitFor(() => expect(H.api.gets.filter((g) => g === "/settings").length).toBeGreaterThan(1));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
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
  });

  it("only while the chat is on screen: a reply that ends in a hidden window stays quiet", async () => {
    H.stream.used = [[PREF]];
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    try {
      render(<ChatPage />);
      await send("how much is the pro plan?", "Reply 1.");
      await screen.findByTestId("memory-used-line");
      await act(async () => {
        await new Promise((r) => setTimeout(r, 20));
      });
      expect(screen.queryByTestId("memory-used-popup")).toBeNull();
    } finally {
      delete (document as unknown as { visibilityState?: unknown }).visibilityState;
    }
  });

  it("the stream hook whitelists the done frame's used", async () => {
    const real = await vi.importActual<typeof import("@/lib/useChatStream")>("@/lib/useChatStream");
    const ev = real.decodeSSE("done", JSON.stringify({ reply: "x", used: [PREF, { kind: "secret", id: "s", label: "no" }] }));
    expect(ev && ev.type === "done" ? ev.used : null).toEqual([PREF]);
    const none = real.decodeSSE("done", JSON.stringify({ reply: "x", used: [] }));
    expect(none && none.type === "done" ? none.used : "absent").toBeUndefined();
  });

  it("a reply with nothing from memory has no line", async () => {
    render(<ChatPage />);
    await send("hello", "Reply 1.");
    expect(screen.queryByTestId("memory-used-line")).toBeNull();
  });
});
