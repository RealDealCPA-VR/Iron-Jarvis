/**
 * v1.329.0 (calm chat W4 F2) — ONE chat list everywhere.
 *
 * Away from the chat surface the sidebar (and the phone nav drawer) used to
 * show a flat "Today / Previous 7 days" list with no projects, no dots and no
 * ages, so a chat running or waiting on the user was invisible off /chat; and
 * a phone /chat had TWO lists (the drawer's and a boxy in-page "Chats (N)"
 * toggle + card rail). Pinned here:
 *  - away from chat: the grouped ThreadGroups list (project headings, a dot
 *    per status, ages), a row opens /chat?thread=<id> and closes the drawer;
 *  - one store per window (lib/chatList): the rail and the drawer read
 *    /chat/threads and /projects ONCE; it paints the chat page's cached list
 *    at once; a thread update re-reads it; a live row is polled;
 *  - on /chat the phone drawer holds the chat page's OWN rail (portal slot,
 *    with search and the row ⋯), opening a row closes the drawer, and the
 *    hidden desktop rail never clears the drawer's slot;
 *  - the chat page: no in-page list on a phone (the aside is md-only), a
 *    quiet top-bar "Chats" opens the drawer, Escape on a row's menu closes
 *    the menu only (never the drawer around it).
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
    nav: { pathname: "/usage", push: (() => {}) as (href: string) => void },
    pushes: [] as string[],
    gets: [] as string[],
    responses: {} as Record<string, unknown>,
    /** path -> a promise the GET waits on (held reads). */
    holds: {} as Record<string, Promise<void>>,
    events: [] as { id: string; type: string }[],
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () => H.nav.pathname,
  useRouter: () => ({ push: (h: string) => H.pushes.push(h), replace: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...(rest as Record<string, never>)}>
      {children}
    </a>
  ),
}));
vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    H.gets.push(path);
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
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: H.events, connected: false }) }));
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async () => ({ reply: "ok", tools_used: [] }),
    abort: () => {},
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
  useProviderHealth: () => ({
    byProvider: {}, signedOutByProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { AppSidebar } from "@/components/AppSidebar";
import { NavDrawer } from "@/components/Sidebar";
import { __resetApiCache, cacheSet } from "@/lib/apiCache";
import { __resetChatList } from "@/lib/chatList";
import { claimChatSlot, closeChatSlot, setChatSlot, useChatSlot } from "@/lib/sidebarSlot";
import { THREAD_POLL_MS } from "@/lib/threadListPoll";
import { LAST_VIEWED_KEY } from "@/lib/threadStatus";

const NOW = Date.now();
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString();

const THREADS = [
  { id: "t1", title: "Quarterly numbers", project_id: "p1", updated_at: iso(30_000), running: true },
  { id: "t2", title: "Lease options", project_id: "p1", updated_at: iso(12 * 60_000), waiting: true },
  { id: "t3", title: "Thank-you email", updated_at: iso(2 * 86_400_000) },
];
const PROJECTS = [{ id: "p1", name: "Q3 Bookkeeping" }];

function setWide(wide: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q.includes("min-width") ? wide : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

function SlotProbe() {
  const slot = useChatSlot();
  return <span data-testid="slot-probe">{slot ? slot.id || "anon" : "none"}</span>;
}

const count = (path: string) => H.gets.filter((g) => g === path).length;

async function openDrawer() {
  await act(async () => {
    window.dispatchEvent(new CustomEvent("ij:toggle-nav"));
  });
  return screen.findByRole("dialog", { name: "Navigation" });
}

beforeEach(() => {
  __resetApiCache();
  __resetChatList();
  setChatSlot(null);
  H.nav.pathname = "/usage";
  H.pushes.length = 0;
  H.gets.length = 0;
  H.events = [];
  for (const k of Object.keys(H.holds)) delete H.holds[k];
  H.responses = {
    "/chat/threads": { threads: THREADS },
    "/projects": { projects: PROJECTS },
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/settings": { settings: {} },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.localStorage.clear();
  window.sessionStorage.clear();
  // Every chat already seen, so the dots below are the daemon's alone.
  window.localStorage.setItem(LAST_VIEWED_KEY, JSON.stringify({ v: 1, since: NOW + 60_000, seen: {} }));
  window.history.replaceState({}, "", "/chat");
  Element.prototype.scrollIntoView = vi.fn();
  setWide(true);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setChatSlot(null);
});

/* ======================================================= away from chat */

describe("away from chat: the same grouped list, with dots and ages", () => {
  it("groups under projects, lights running and waiting, and shows ages", async () => {
    render(<AppSidebar />);
    const list = await screen.findByTestId("sidebar-recent-chats");
    await within(list).findByTitle("Quarterly numbers");
    const headings = [...list.querySelectorAll("h3")].map((h) => h.textContent);
    expect(headings).toEqual(["Q3 Bookkeeping", "No project"]);
    expect(within(list).getByTestId("thread-dot-t1").getAttribute("data-status")).toBe("running");
    expect(within(list).getByTestId("thread-dot-t2").getAttribute("data-status")).toBe("waiting");
    expect(within(list).getByTestId("thread-dot-t3").getAttribute("data-status")).toBe("idle");
    expect(within(list).getByTestId("thread-row-t1").textContent).toContain("now");
    expect(within(list).getByTestId("thread-row-t2").textContent).toContain("12m");
    expect(within(list).getByTestId("thread-row-t3").textContent).toContain("2d");
  });

  it("an unread chat lights up away from chat too (this browser's stamps)", async () => {
    window.localStorage.setItem(LAST_VIEWED_KEY, JSON.stringify({ v: 1, since: 0, seen: {} }));
    render(<AppSidebar />);
    const list = await screen.findByTestId("sidebar-recent-chats");
    await within(list).findByTitle("Thank-you email");
    expect(within(list).getByTestId("thread-dot-t3").getAttribute("data-status")).toBe("unread");
  });

  it("pinned chats come first, under Pinned (the chat page's own pins)", async () => {
    window.localStorage.setItem("ij_chat_pinned", JSON.stringify(["t3"]));
    render(<AppSidebar />);
    const list = await screen.findByTestId("sidebar-recent-chats");
    await waitFor(() => expect(list.querySelector("h3")?.textContent).toBe("Pinned"));
  });

  it("in the phone drawer: a row opens its chat and the drawer closes", async () => {
    setWide(false);
    render(<NavDrawer />);
    const drawer = await openDrawer();
    fireEvent.click(await within(drawer).findByTitle("Lease options"));
    expect(H.pushes).toEqual(["/chat?thread=t2"]);
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull());
  });

  it("the rail and the drawer share ONE read of the chats and the projects", async () => {
    setWide(false);
    render(
      <>
        <AppSidebar />
        <NavDrawer />
      </>,
    );
    const drawer = await openDrawer();
    await within(drawer).findByTitle("Quarterly numbers");
    await within(screen.getByTestId("app-sidebar")).findByTitle("Quarterly numbers");
    expect(count("/chat/threads")).toBe(1);
    expect(count("/projects")).toBe(1);
  });

  it("paints the list the chat page last read at once (lib/apiCache), before any answer", async () => {
    cacheSet("/chat/threads", { threads: [{ id: "c1", title: "From the chat page", updated_at: iso(60_000) }] });
    let release = () => {};
    H.holds["/chat/threads"] = new Promise<void>((r) => (release = r));
    render(<AppSidebar />);
    const list = await screen.findByTestId("sidebar-recent-chats");
    expect(within(list).getByTitle("From the chat page")).toBeTruthy();
    await act(async () => release());
    expect(await within(list).findByTitle("Quarterly numbers")).toBeTruthy();
  });

  it("a thread update re-reads the list", async () => {
    const { rerender } = render(<AppSidebar />);
    await within(await screen.findByTestId("sidebar-recent-chats")).findByTitle("Quarterly numbers");
    expect(count("/chat/threads")).toBe(1);
    H.responses["/chat/threads"] = { threads: [...THREADS, { id: "t4", title: "A new one", updated_at: iso(1000) }] };
    H.events = [{ id: "e1", type: "chat.thread_updated" }];
    rerender(<AppSidebar />);
    expect(await within(screen.getByTestId("sidebar-recent-chats")).findByTitle("A new one")).toBeTruthy();
    expect(count("/chat/threads")).toBe(2);
  });

  it("updates during a read cost ONE more read after it (never one each), and its answer is shown", async () => {
    let release = () => {};
    H.holds["/chat/threads"] = new Promise<void>((r) => (release = r));
    const { rerender } = render(<AppSidebar />);
    await act(async () => {});
    expect(count("/chat/threads")).toBe(1);
    H.responses["/chat/threads"] = { threads: [...THREADS, { id: "t5", title: "Landed meanwhile", updated_at: iso(500) }] };
    H.events = [{ id: "e1", type: "chat.thread_updated" }];
    rerender(<AppSidebar />);
    H.events = [{ id: "e2", type: "chat.thread_updated" }, { id: "e1", type: "chat.thread_updated" }];
    rerender(<AppSidebar />);
    await act(async () => {});
    expect(count("/chat/threads")).toBe(1); // joined, not sent again mid-read
    await act(async () => release());
    expect(await within(await screen.findByTestId("sidebar-recent-chats")).findByTitle("Landed meanwhile")).toBeTruthy();
    expect(count("/chat/threads")).toBe(2);
  });

  it("while a chat is running the list is re-read every THREAD_POLL_MS (one read for two lists)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setWide(false);
    render(
      <>
        <AppSidebar />
        <NavDrawer />
      </>,
    );
    await openDrawer();
    await within(screen.getByTestId("app-sidebar")).findByTitle("Quarterly numbers");
    expect(count("/chat/threads")).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THREAD_POLL_MS + 50);
    });
    expect(count("/chat/threads")).toBe(2);
    // CONTROL: nothing live, no more reads.
    H.responses["/chat/threads"] = { threads: [THREADS[2]] };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THREAD_POLL_MS + 50);
    });
    const after = count("/chat/threads");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THREAD_POLL_MS * 3);
    });
    expect(count("/chat/threads")).toBe(after);
  });
});

/* ======================================================= the slots */

describe("on /chat the drawer holds the chat page's own list", () => {
  it("the open drawer publishes its slot, and closeChatSlot() closes the drawer", async () => {
    H.nav.pathname = "/chat";
    setWide(false);
    render(
      <>
        <NavDrawer />
        <SlotProbe />
      </>,
    );
    expect(screen.getByTestId("slot-probe").textContent).toBe("none");
    await openDrawer();
    await waitFor(() => expect(screen.getByTestId("slot-probe").textContent).toBe("ij-drawer-chat-slot"));
    await act(async () => closeChatSlot());
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull());
    await waitFor(() => expect(screen.getByTestId("slot-probe").textContent).toBe("none"));
  });

  it("on /chat the sidebar reads no chats of its own (the page's rail is the list), only projects", async () => {
    H.nav.pathname = "/chat";
    render(<AppSidebar />);
    expect(await screen.findByTestId("sidebar-projects")).toBeTruthy();
    await act(async () => {});
    expect(count("/projects")).toBe(1);
    expect(count("/chat/threads")).toBe(0);
    expect(screen.queryByTestId("sidebar-recent-chats")).toBeNull();
  });

  it("the hidden desktop rail on a phone never clears the drawer's slot", async () => {
    H.nav.pathname = "/chat";
    setWide(false);
    const { unmount } = render(<AppSidebar />);
    const drawerSlot = document.createElement("div");
    drawerSlot.id = "drawer";
    claimChatSlot(drawerSlot);
    render(<SlotProbe />);
    expect(screen.getByTestId("slot-probe").textContent).toBe("drawer");
    unmount(); // the rail goes (a navigation): its release must not touch the drawer's slot
    await act(async () => {});
    expect(screen.getByTestId("slot-probe").textContent).toBe("drawer");
  });
});

/* ======================================================= the chat page */

describe("the chat page on a phone: one list, in the drawer", () => {
  it("no boxy in-page toggle; the in-page rail is md-only; a quiet top-bar Chats opens the drawer", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    const aside = screen.getByTestId("chat-rail-aside");
    expect(aside.contains(rail)).toBe(true);
    const cls = (aside.getAttribute("class") ?? "").split(/\s+/);
    expect(cls).toContain("hidden");
    expect(cls).toContain("md:block");
    // The v1.215 card is gone from the rail.
    expect(rail.getAttribute("class") ?? "").not.toContain("card-surface");
    expect(screen.queryByRole("button", { name: /^Hide chats$/ })).toBeNull();
    const door = screen.getByTestId("chat-open-chats");
    expect((door.getAttribute("class") ?? "").split(/\s+/)).toContain("md:hidden");
    expect(door.closest('[data-testid="chat-topbar"]')).not.toBeNull();
    await waitFor(() => expect(door.getAttribute("aria-label")).toBe("Chats (3)"));
    const seen = vi.fn();
    window.addEventListener("ij:toggle-nav", seen);
    fireEvent.click(door);
    window.removeEventListener("ij:toggle-nav", seen);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("in the drawer's slot the rail keeps search and the row ⋯, and opening a chat closes the drawer", async () => {
    H.responses["/chat/threads/t3"] = {
      id: "t3",
      title: "Thank-you email",
      messages: [{ role: "assistant", content: "The thank-you draft." }],
    };
    const slot = document.createElement("div");
    slot.id = "ij-drawer-chat-slot";
    document.body.appendChild(slot);
    const close = vi.fn();
    claimChatSlot(slot, close);
    try {
      render(<ChatPage />);
      const rail = await screen.findByTestId("chat-thread-rail");
      expect(slot.contains(rail)).toBe(true);
      expect(screen.queryByTestId("chat-open-chats")).toBeNull();
      expect(within(rail).getByLabelText("Search chats")).toBeTruthy();
      await within(rail).findByTitle("Thank-you email");
      expect(within(rail).getAllByRole("button", { name: /^Options for / }).length).toBe(3);
      fireEvent.click(within(rail).getByTitle("Thank-you email"));
      expect(await screen.findByText("The thank-you draft.")).toBeTruthy();
      expect(close).toHaveBeenCalled();
    } finally {
      setChatSlot(null);
      slot.remove();
    }
  });

  it("Escape on a row's menu closes the menu only, never the drawer around it", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Thank-you email");
    const drawerEscape = vi.fn();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") drawerEscape();
    };
    window.addEventListener("keydown", onKey); // the drawer's listener (bubble)
    try {
      fireEvent.click(within(rail).getByRole("button", { name: "Options for Thank-you email" }));
      expect(await screen.findByRole("menu", { name: "Options for Thank-you email" })).toBeTruthy();
      fireEvent.keyDown(document.body, { key: "Escape" });
      await waitFor(() => expect(screen.queryByRole("menu", { name: "Options for Thank-you email" })).toBeNull());
      expect(drawerEscape).not.toHaveBeenCalled();
      // CONTROL: with no menu open, Escape reaches it as before.
      fireEvent.keyDown(document.body, { key: "Escape" });
      expect(drawerEscape).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("keydown", onKey);
    }
  });
});
