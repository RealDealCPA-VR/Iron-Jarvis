/**
 * v1.446.0 (ease-of-use review item 10) — old chats never disappear.
 *
 * Before: the chat list loaded the newest 100 chats and "Search chats"
 * filtered those 100 titles on the page, so an older chat vanished and the
 * words said inside a chat were never found. Pinned here:
 *  - lib/chatList's paging rules: the first older page uses a bare-stamp
 *    cursor, later ones the daemon's `next`; a fresh page 1 KEEPS the older
 *    pages (a chat pushed past the 100th place is not lost; a newer row the
 *    fresh page no longer holds goes); tied rows never appear twice;
 *  - the sidebar list and the chat page's rail end in "Show older chats"
 *    when they hold a full page, and pressing it appends the next page;
 *  - "Search chats" asks GET /chat/threads/search (debounced, two characters
 *    or more, the previous request cancelled) and shows its answer with the
 *    matched words in the list's place; clearing the box brings the list
 *    back; when the search cannot be reached the titles are still filtered
 *    and one quiet line says so.
 */

import { type ReactNode } from "react";
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
    gets: [] as string[],
    signals: {} as Record<string, AbortSignal | undefined>,
    responses: {} as Record<string, unknown>,
    holds: {} as Record<string, Promise<void>>,
    pushes: [] as string[],
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () => "/usage",
  useRouter: () => ({ push: (h: string) => H.pushes.push(h), replace: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string, opts?: { signal?: AbortSignal }) => {
    H.gets.push(path);
    H.signals[path] = opts?.signal;
    const hold = H.holds[path];
    if (hold) await hold;
    const r = H.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t-new", title: "t", updated_at: "2026-10-09T10:00:00" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false, text: "", tools: [], approval: null,
    run: () => Promise.resolve({ reply: "ok", tools_used: [] }),
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
vi.mock("@/components/project/ProjectSurfaces", () => ({
  ProjectSurface: ({ view }: { view: string }) => <div data-testid="surface-stub">surface:{view}</div>,
}));

import ChatPage from "@/app/chat/page";
import SidebarChats from "@/components/chat/SidebarChats";
import { __resetApiCache } from "@/lib/apiCache";
import {
  CHAT_LIST_PAGE,
  CHAT_SEARCH_DEBOUNCE_MS,
  CHAT_SEARCH_FALLBACK_WORDS,
  SHOW_OLDER_WORDS,
  __resetChatList,
  mayShowOlder,
  noOlderPages,
  olderCursor,
  olderThreadsPath,
  snippetParts,
  withFreshFirstPage,
  withOlderPage,
} from "@/lib/chatList";
import { LAST_VIEWED_KEY } from "@/lib/threadStatus";

/* ------------------------------------------------------------------ data */

const BASE = Date.UTC(2026, 10, 1, 12, 0, 0);
/** The daemon's naive UTC stamp `i` seconds after BASE. */
const at = (secs: number) => new Date(BASE + secs * 1000).toISOString().replace("Z", "");

/** 100 chats, newest first (`n099` newest ... `n000` oldest of page 1). */
/** A list row as the daemon draws it (only what these tests read). */
type Row = { id: string; title: string; project_id?: string | null; updated_at: string; messages: number };

const PAGE1: Row[] = Array.from({ length: CHAT_LIST_PAGE }, (_, k) => {
  const i = CHAT_LIST_PAGE - 1 - k;
  return { id: `n${String(i).padStart(3, "0")}`, title: `Recent chat ${i}`, updated_at: at(1000 + i), messages: 2 };
});
const LAST = PAGE1[PAGE1.length - 1];
/** The older page: one row tied with page 1's last (comes again), two older
 *  chats in their own project (their own group, so they are on screen). */
const OLD_A: Row = { id: "old1", title: "Van depreciation", project_id: "p9", updated_at: at(10), messages: 4 };
const OLD_B: Row = { id: "old2", title: "Lease review 2023", project_id: "p9", updated_at: at(5), messages: 3 };
const OLDER_PATH = olderThreadsPath("/chat/threads", LAST.updated_at);

const searchPath = (q: string) => `/chat/threads/search?q=${encodeURIComponent(q)}`;

async function settle(ms = 0): Promise<void> {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, ms));
    });
  }
}

beforeEach(() => {
  __resetApiCache();
  __resetChatList();
  H.gets.length = 0;
  H.pushes.length = 0;
  for (const k of Object.keys(H.signals)) delete H.signals[k];
  for (const k of Object.keys(H.holds)) delete H.holds[k];
  H.responses = {
    "/chat/threads": { threads: PAGE1 },
    "/projects": { projects: [{ id: "p9", name: "Old books" }] },
    [OLDER_PATH]: { threads: [LAST, OLD_A, OLD_B], next: null },
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/settings": { settings: {} },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
    [searchPath("zanzibar")]: {
      threads: [{ ...OLD_A, match: "text", snippet: "use the [zanzibar] method here" }],
      text: true,
    },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.localStorage.setItem(LAST_VIEWED_KEY, JSON.stringify({ v: 1, since: BASE + 1e9, seen: {} }));
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

/* ============================================================ the rules */

describe("lib/chatList paging rules", () => {
  it("a full page may hold more; the first cursor is the last row's bare stamp, then the daemon's next", () => {
    const pages = noOlderPages<Row>(PAGE1.length);
    expect(mayShowOlder(pages)).toBe(true);
    expect(olderCursor(PAGE1, pages)).toBe(LAST.updated_at);
    expect(mayShowOlder(noOlderPages(40))).toBe(false);
    const after = withOlderPage(PAGE1, pages, { threads: [LAST, OLD_A], next: "x|old1" });
    // The tied row the bare cursor brought again is dropped by id.
    expect(after.threads.filter((t) => t.id === LAST.id)).toHaveLength(1);
    expect(after.threads.at(-1)?.id).toBe("old1");
    expect(olderCursor(after.threads, after.pages)).toBe("x|old1");
    const end = withOlderPage(after.threads, after.pages, { threads: [OLD_B], next: null });
    expect(mayShowOlder(end.pages)).toBe(false);
    expect(olderCursor(end.threads, end.pages)).toBeNull();
    expect(OLDER_PATH).toBe(`/chat/threads?limit=100&before=${encodeURIComponent(LAST.updated_at)}`);
    expect(olderThreadsPath("/chat/threads?archived=only", "c")).toBe(
      "/chat/threads?archived=only&limit=100&before=c",
    );
  });

  it("a fresh page 1 keeps the older pages and the chat it pushed past the edge; a newer row it lost goes", () => {
    const loaded = withOlderPage(PAGE1, noOlderPages<Row>(PAGE1.length), { threads: [OLD_A], next: null });
    // Two new chats arrived and n050 was deleted: page 1 is still 100 rows,
    // and the old 100th row (LAST) is now the 101st.
    const fresh = [
      { id: "brand-new-1", title: "Just now", updated_at: at(5001) },
      { id: "brand-new-2", title: "Just now too", updated_at: at(5000) },
      ...PAGE1.filter((t) => t.id !== "n050" && t.id !== LAST.id),
    ];
    expect(fresh).toHaveLength(CHAT_LIST_PAGE);
    const merged = withFreshFirstPage(fresh, loaded.threads, loaded.pages);
    const ids = merged.threads.map((t) => t.id);
    expect(ids).toContain("old1"); // an older page survives the refresh
    expect(ids).toContain(LAST.id); // pushed from the 100th place to the 101st
    expect(ids).not.toContain("n050"); // newer than the edge and gone: deleted
    expect(new Set(ids).size).toBe(ids.length);
    // An older chat deleted from the list since its page was read stays gone.
    const afterDelete = loaded.threads.filter((t) => t.id !== "old1");
    const again = withFreshFirstPage(PAGE1, afterDelete, loaded.pages);
    expect(again.threads.map((t) => t.id)).not.toContain("old1");
    // Nothing older read: page 1 is the list, exactly.
    const plain = withFreshFirstPage(PAGE1, [], noOlderPages<Row>());
    expect(plain.threads).toEqual(PAGE1);
  });

  it("a snippet's marked words are parts, never HTML", () => {
    expect(snippetParts("a [b] c <i>x</i>")).toEqual([
      { text: "a ", marked: false },
      { text: "b", marked: true },
      { text: " c <i>x</i>", marked: false },
    ]);
  });
});

/* ======================================================= the sidebar list */

describe("the sidebar list", () => {
  it("ends in Show older chats and pressing it appends the next page", async () => {
    render(<SidebarChats />);
    const btn = await screen.findByTestId("sidebar-show-older");
    expect(btn.textContent).toBe(SHOW_OLDER_WORDS);
    fireEvent.click(btn);
    await screen.findByTitle("Van depreciation");
    expect(H.gets).toContain(OLDER_PATH);
    // The daemon said none are left: the button goes.
    await waitFor(() => expect(screen.queryByTestId("sidebar-show-older")).toBeNull());
  });

  it("search asks the daemon once after the pause, finds a chat the list does not hold, and shows the matched words", async () => {
    render(<SidebarChats />);
    const box = (await screen.findByLabelText("Search chats")) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "z" } });
    fireEvent.change(box, { target: { value: "za" } });
    // Typing on within the pause: "za" is never asked for.
    await settle(50);
    fireEvent.change(box, { target: { value: "zanzibar" } });
    expect(screen.queryByTitle("Van depreciation")).toBeNull();
    await screen.findByTitle("Van depreciation", undefined, { timeout: 3000 });
    const searches = H.gets.filter((g) => g.startsWith("/chat/threads/search"));
    expect(searches).toEqual([searchPath("zanzibar")]);
    const detail = screen.getByTestId("thread-detail-old1");
    expect(detail.textContent).toContain("use the zanzibar method");
    expect(within(detail).getByText("zanzibar").tagName).toBe("MARK");
    // Clearing the box brings the list back.
    fireEvent.change(box, { target: { value: "" } });
    await waitFor(() => expect(screen.queryByTitle("Van depreciation")).toBeNull());
  });

  it("one character asks nothing", async () => {
    render(<SidebarChats />);
    const box = await screen.findByLabelText("Search chats");
    fireEvent.change(box, { target: { value: "z" } });
    await settle(CHAT_SEARCH_DEBOUNCE_MS + 100);
    expect(H.gets.some((g) => g.startsWith("/chat/threads/search"))).toBe(false);
  });

  it("a newer search cancels the one before it, whose late answer is dropped", async () => {
    let release = () => {};
    H.holds[searchPath("van")] = new Promise<void>((r) => {
      release = r;
    });
    H.responses[searchPath("van")] = { threads: [{ ...OLD_B, match: "text", snippet: "stale" }] };
    render(<SidebarChats />);
    const box = await screen.findByLabelText("Search chats");
    fireEvent.change(box, { target: { value: "van" } });
    await waitFor(() => expect(H.gets).toContain(searchPath("van")), { timeout: 3000 });
    fireEvent.change(box, { target: { value: "zanzibar" } });
    await screen.findByTitle("Van depreciation", undefined, { timeout: 3000 });
    expect(H.signals[searchPath("van")]?.aborted).toBe(true);
    release();
    await settle();
    expect(screen.queryByTitle("Lease review 2023")).toBeNull();
  });

  it("when the search cannot be reached the titles are still filtered, and one line says so", async () => {
    H.responses[searchPath("recent chat 9")] = new H.FakeApiError("down", 500);
    render(<SidebarChats />);
    const box = await screen.findByLabelText("Search chats");
    fireEvent.change(box, { target: { value: "recent chat 9" } });
    expect(screen.getByTitle("Recent chat 9")).toBeTruthy(); // at once
    expect((await screen.findByTestId("sidebar-chat-search-fallback", undefined, { timeout: 3000 })).textContent).toBe(
      CHAT_SEARCH_FALLBACK_WORDS,
    );
    expect(screen.getByTitle("Recent chat 9")).toBeTruthy();
  });
});

/* ===================================================== the chat page rail */

describe("the chat page's rail", () => {
  it("ends in Show older chats; pressing it appends the next page", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    const btn = await within(rail).findByTestId("thread-show-older", undefined, { timeout: 5000 });
    fireEvent.click(btn);
    await within(rail).findByTitle("Van depreciation");
    expect(H.gets).toContain(OLDER_PATH);
    await waitFor(() => expect(within(rail).queryByTestId("thread-show-older")).toBeNull());
  });

  it("Search chats shows the daemon's answer with the matched words; clearing brings the list back", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    const box = await within(rail).findByLabelText("Search chats", {}, { timeout: 5000 });
    fireEvent.change(box, { target: { value: "zanzibar" } });
    await within(rail).findByTitle("Van depreciation", undefined, { timeout: 3000 });
    expect(within(rail).getByTestId("thread-detail-old1").textContent).toContain("zanzibar");
    // The older chat's ⋯ menu is there (it is listed while the search shows).
    expect(within(rail).getByRole("button", { name: "Options for Van depreciation" })).toBeTruthy();
    fireEvent.change(box, { target: { value: "" } });
    await waitFor(() => expect(within(rail).queryByTitle("Van depreciation")).toBeNull());
  });

  it("when the search cannot be reached the rail says so and keeps filtering titles", async () => {
    H.responses[searchPath("recent chat 9")] = new H.FakeApiError("down", 500);
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    const box = await within(rail).findByLabelText("Search chats", {}, { timeout: 5000 });
    fireEvent.change(box, { target: { value: "recent chat 9" } });
    expect(within(rail).getByTitle("Recent chat 9")).toBeTruthy();
    expect(
      (await within(rail).findByTestId("thread-search-fallback", undefined, { timeout: 3000 })).textContent,
    ).toBe(CHAT_SEARCH_FALLBACK_WORDS);
  });
});
