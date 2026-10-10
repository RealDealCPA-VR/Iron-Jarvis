/**
 * v1.329.0 (calm chat W5 G3) — the chat list keeps ONE shape, shows work
 * elsewhere, and tells same-titled chats apart.
 *
 *  - inside a project chat on /chat the rail is the grouped list: that
 *    project's group first and open, every other group folded to a heading
 *    that still shows the most urgent dot of its chats and how many it holds;
 *    the heading opens it; a search opens every group; "Only <project>" is a
 *    filter (nothing re-read) with a one-press way back; a row in another
 *    group keeps its ⋯ and its actions (a messaging chat is renamed with a
 *    title-only body, as in its own project); a live row in a folded group
 *    keeps the list polling;
 *  - off /chat the sidebar list has a quiet search box (filters in place;
 *    Escape clears it before it closes the drawer);
 *  - two or more VISIBLE rows with one title get a quiet second part (the
 *    time of day, or the day), in both lists; a unique title gets none;
 *  - the phone drawer scrolls as ONE column: its slot says so
 *    (`data-flow="column"`) and the chat page then gives the list no scroll
 *    box of its own; the wide rail keeps the old layout.
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
    nav: { pathname: "/chat" },
    pushes: [] as string[],
    gets: [] as string[],
    puts: [] as { path: string; body: Record<string, unknown> }[],
    responses: {} as Record<string, unknown>,
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
    const r = H.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t-new", title: "t", updated_at: "2026-10-09T10:00:00" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
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
import ThreadGroups, { groupStatus } from "@/components/chat/ThreadGroups";
import { __resetApiCache } from "@/lib/apiCache";
import { __resetChatList } from "@/lib/chatList";
import { sameTitleLabels } from "@/lib/sameTitleRows";
import { claimChatSlot, setChatSlot } from "@/lib/sidebarSlot";
import { THREAD_POLL_MS } from "@/lib/threadListPoll";
import { LAST_VIEWED_KEY } from "@/lib/threadStatus";

/* ------------------------------------------------------------------ data */

const NOW = Date.now();
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const MIN = 60_000;

const PROJECTS = [
  { id: "p1", name: "Harbor Street Cafe" },
  { id: "p2", name: "Pier 9" },
];
const A1 = { id: "a1", title: "Q3 revenue by location", project_id: "p1", updated_at: ago(5 * MIN), messages: 2 };
const A2 = { id: "a2", title: "Weekly staff schedule", project_id: "p1", updated_at: ago(60 * MIN), messages: 2 };
const B1 = { id: "b1", title: "Lease options", project_id: "p2", updated_at: ago(20 * MIN), messages: 2, waiting: true };
const M1 = {
  id: "m1",
  title: "Phone chat",
  project_id: "p2",
  updated_at: ago(30 * MIN),
  messages: 2,
  owner: "daemon",
  comm_channel: "telegram",
};
const N1 = { id: "n1", title: "Check what changed in the repo", updated_at: ago(31 * MIN), messages: 2, running: true };
const N2 = { id: "n2", title: "Check what changed in the repo", updated_at: ago(3 * 60 * MIN), messages: 2 };

const SCOPED = { threads: [A1, A2] };
const ALL = { threads: [A1, A2, B1, M1, N1, N2] };

/* --------------------------------------------------------------- helpers */

async function settle(): Promise<void> {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

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

const groupOrder = (rail: HTMLElement) =>
  within(rail)
    .getAllByRole("region")
    .map((s) => s.getAttribute("data-testid"));

async function projectRail(): Promise<HTMLElement> {
  window.localStorage.setItem("ij_chat_project", "p1");
  render(<ChatPage />);
  const rail = await screen.findByTestId("chat-thread-rail");
  await within(rail).findByTitle("Q3 revenue by location");
  // The folded groups come from the second (unscoped) read.
  await waitFor(() => expect(within(rail).getByTestId("thread-group-p:p2")).toBeTruthy());
  return rail;
}

beforeEach(() => {
  __resetApiCache();
  __resetChatList();
  setChatSlot(null);
  H.nav.pathname = "/chat";
  H.pushes.length = 0;
  H.gets.length = 0;
  H.puts.length = 0;
  H.responses = {
    "/chat/threads": ALL,
    "/chat/threads?project_id=p1": SCOPED,
    "/projects": { projects: PROJECTS },
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/settings": { settings: {} },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.localStorage.clear();
  window.sessionStorage.clear();
  // Every chat already seen: the dots below are the daemon's alone.
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

/* ======================================== (1) the rail in a project chat */

describe("inside a project chat the rail is the grouped list", () => {
  it("the project's group comes first and open; the others are folded, each heading lit by its chats", async () => {
    const rail = await projectRail();
    expect(groupOrder(rail)).toEqual(["thread-group-p:p1", "thread-group-p:p2", "thread-group-none"]);
    // Lead: open, its rows on screen, never foldable.
    expect(within(rail).getByTitle("Weekly staff schedule")).toBeTruthy();
    expect(within(rail).queryByTestId("thread-group-toggle-p:p1")).toBeNull();
    // Others: folded, no rows, but the heading carries the most urgent dot.
    expect(screen.getByTestId("thread-group-p:p2").getAttribute("data-folded")).toBe("true");
    expect(within(rail).queryByTestId("thread-row-b1")).toBeNull();
    expect(screen.getByTestId("thread-group-dot-p:p2").getAttribute("data-status")).toBe("waiting");
    expect(screen.getByTestId("thread-group-dot-none").getAttribute("data-status")).toBe("running");
    const toggle = screen.getByTestId("thread-group-toggle-none");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // Words as well as colour, and how many chats the group holds.
    expect(toggle.textContent).toContain("Working on it");
    expect(toggle.textContent).toContain("2");
  });

  it("pressing a folded heading opens it in place, with each chat's own dot", async () => {
    const rail = await projectRail();
    fireEvent.click(screen.getByTestId("thread-group-toggle-p:p2"));
    expect(await within(rail).findByTitle("Lease options")).toBeTruthy();
    expect(screen.getByTestId("thread-dot-b1").getAttribute("data-status")).toBe("waiting");
    expect(screen.getByTestId("thread-group-toggle-p:p2").getAttribute("aria-expanded")).toBe("true");
    expect(screen.queryByTestId("thread-group-dot-p:p2")).toBeNull();
    // ...and folds again.
    fireEvent.click(screen.getByTestId("thread-group-toggle-p:p2"));
    await waitFor(() => expect(within(rail).queryByTitle("Lease options")).toBeNull());
  });

  it("a search opens every group to show its matches", async () => {
    const rail = await projectRail();
    fireEvent.change(within(rail).getByLabelText("Search chats"), { target: { value: "lease" } });
    expect(within(rail).getByTitle("Lease options")).toBeTruthy();
    expect(within(rail).queryByTitle("Q3 revenue by location")).toBeNull();
  });

  it("'Only <project>' is a filter: the other groups go, nothing is re-read, one press brings them back", async () => {
    const rail = await projectRail();
    await settle();
    const reads = H.gets.length;
    fireEvent.click(within(rail).getByRole("button", { name: "Only Harbor Street Cafe" }));
    await waitFor(() => expect(within(rail).queryByTestId("thread-group-none")).toBeNull());
    // v1.329.0 (W6 H1): the filter says which chats it shows by which of its
    // two ghosts is filled (no third "Threads in ..." label).
    expect(within(rail).getByRole("button", { name: "Only Harbor Street Cafe" }).getAttribute("aria-pressed")).toBe("true");
    expect(within(rail).getByTitle("Q3 revenue by location")).toBeTruthy();
    await settle();
    expect(H.gets.slice(reads).filter((p) => p.startsWith("/chat/threads"))).toEqual([]);
    fireEvent.click(within(rail).getByRole("button", { name: "All chats" }));
    expect(await within(rail).findByTestId("thread-group-dot-none")).toBeTruthy();
  });

  it("a row in another group keeps its ⋯: a messaging chat there is renamed with a title-only body", async () => {
    H.responses["/chat/threads/m1"] = { id: "m1", title: "Phone chat", messages: [{ role: "user", content: "hi" }] };
    const rail = await projectRail();
    fireEvent.click(screen.getByTestId("thread-group-toggle-p:p2"));
    await within(rail).findByTitle("Phone chat");
    fireEvent.click(within(rail).getByRole("button", { name: "Options for Phone chat" }));
    const menu = await screen.findByRole("menu", { name: "Options for Phone chat" });
    fireEvent.click(within(menu).getByText(/Rename/));
    const box = (await within(rail).findByLabelText("Rename chat")) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "Phone chat, renamed" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.puts.some((p) => p.path === "/chat/threads/m1")).toBe(true));
    const put = H.puts.find((p) => p.path === "/chat/threads/m1")!;
    // The daemon owns a messaging chat's messages: a body with `messages`
    // would be refused (409).
    expect(put.body).toEqual({ title: "Phone chat, renamed" });
  });

  it("a live chat in a folded group keeps the list polling", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // Nothing live in the project itself; one waiting chat elsewhere.
    H.responses["/chat/threads"] = { threads: [A1, A2, B1] };
    await projectRail();
    await settle();
    const scopedReads = () => H.gets.filter((p) => p === "/chat/threads?project_id=p1").length;
    const start = scopedReads();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THREAD_POLL_MS);
    });
    await waitFor(() => expect(scopedReads()).toBeGreaterThan(start));
  });

  it("CONTROL: with nothing live anywhere, nothing polls", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    H.responses["/chat/threads"] = { threads: [A1, A2, { ...B1, waiting: false }] };
    await projectRail();
    await settle();
    const start = H.gets.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THREAD_POLL_MS * 3);
    });
    expect(H.gets.length).toBe(start);
  });

  it("CONTROL: with no project the list is grouped and nothing is folded", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Lease options");
    expect(within(rail).queryByTestId("thread-group-toggle-none")).toBeNull();
    expect(within(rail).getByTitle("Q3 revenue by location")).toBeTruthy();
  });
});

/* =================================== (3) same-titled rows read apart */

describe("same-titled chats read apart", () => {
  it("in the chat page's list, the two 'Check what changed' rows each get their time", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTestId("thread-row-n1");
    const one = screen.getByTestId("thread-twin-n1").textContent;
    const two = screen.getByTestId("thread-twin-n2").textContent;
    expect(one).toBeTruthy();
    expect(two).toBeTruthy();
    expect(one).not.toBe(two);
    // A title of its own gets nothing.
    expect(screen.queryByTestId("thread-twin-b1")).toBeNull();
  });

  it("in the sidebar list off /chat too", async () => {
    H.nav.pathname = "/usage";
    render(<AppSidebar />);
    const list = await screen.findByTestId("sidebar-recent-chats");
    await within(list).findByTestId("thread-row-n1");
    expect(within(list).getByTestId("thread-twin-n1").textContent).toBeTruthy();
    expect(within(list).getByTestId("thread-twin-n2").textContent).toBeTruthy();
    expect(within(list).queryByTestId("thread-twin-a1")).toBeNull();
  });

  it("sameTitleLabels: a shared day says the time, a day alone says the day, never both unless they tie", () => {
    const at = (d: number, h: number, m: number) => new Date(2026, 9, d, h, m).toISOString();
    const now = new Date(2026, 9, 9, 22, 0).getTime();
    const today = sameTitleLabels(
      [
        { id: "x", title: "Same", updated_at: at(9, 20, 48) },
        { id: "y", title: " same ", updated_at: at(9, 15, 5) },
        { id: "z", title: "Different", updated_at: at(9, 15, 5) },
      ],
      now,
    );
    expect(today.get("x")).toBe("8:48 PM");
    expect(today.get("y")).toBe("3:05 PM");
    expect(today.has("z")).toBe(false);

    const yesterday = sameTitleLabels(
      [
        { id: "x", title: "Same", updated_at: at(8, 9, 0) },
        { id: "y", title: "Same", updated_at: at(8, 16, 30) },
      ],
      now,
    );
    // v1.329.0 (W6 H1 fix round): ONE short part on every row. These were
    // "Yesterday · 9:00 AM" / "Yesterday · 4:30 PM", which clipped the title
    // to about 10 characters from the day after the chats were made. The
    // day now lives only in the tooltip (sameTitleTooltip).
    expect(yesterday.get("x")).toBe("9:00 AM");
    expect(yesterday.get("y")).toBe("4:30 PM");

    const mixed = sameTitleLabels(
      [
        { id: "x", title: "Same", updated_at: at(9, 9, 0) },
        { id: "y", title: "Same", updated_at: at(6, 9, 0) },
        { id: "w", title: "Same", updated_at: at(6, 16, 30) },
      ],
      now,
    );
    // Same fix round: the two that share Oct 6 say only their time (were
    // "Oct 6 · 9:00 AM" / "Oct 6 · 4:30 PM"); the one alone on its day says
    // only the day.
    expect(mixed.get("x")).toBe("Today");
    expect(mixed.get("y")).toBe("9:00 AM");
    expect(mixed.get("w")).toBe("4:30 PM");

    // The rare tie: twins on DIFFERENT days that would read the same time
    // get the day as well, and only those rows.
    const tie = sameTitleLabels(
      [
        { id: "a", title: "Same", updated_at: at(8, 20, 48) },
        { id: "b", title: "Same", updated_at: at(8, 9, 0) },
        { id: "c", title: "Same", updated_at: at(6, 20, 48) },
        { id: "d", title: "Same", updated_at: at(6, 11, 15) },
      ],
      now,
    );
    expect(tie.get("a")).toBe("Yesterday · 8:48 PM");
    expect(tie.get("c")).toBe("Oct 6 · 8:48 PM");
    expect(tie.get("b")).toBe("9:00 AM");
    expect(tie.get("d")).toBe("11:15 AM");

    // Made in the same minute. v1.329.0 (W6 H1): the label is always the
    // short time (it stands in for the age on a narrow row); the seconds
    // moved to the label's tooltip (sameTitleTooltip).
    const sameMinute = sameTitleLabels(
      [
        { id: "x", title: "Same", updated_at: new Date(2026, 9, 9, 18, 11, 5).toISOString() },
        { id: "y", title: "Same", updated_at: new Date(2026, 9, 9, 18, 11, 42).toISOString() },
        { id: "w", title: "Same", updated_at: new Date(2026, 9, 9, 20, 48, 0).toISOString() },
      ],
      now,
    );
    expect(sameMinute.get("x")).toBe("6:11 PM");
    expect(sameMinute.get("y")).toBe("6:11 PM");
    expect(sameMinute.get("w")).toBe("8:48 PM");

    // Untitled chats share the list's "Untitled chat".
    const untitled = sameTitleLabels(
      [
        { id: "x", title: "", updated_at: at(9, 9, 0) },
        { id: "y", title: null, updated_at: at(9, 10, 0) },
      ],
      now,
    );
    expect(untitled.size).toBe(2);
  });

  it("only VISIBLE rows count: a twin inside a folded group gives the open row no label", () => {
    render(
      <ThreadGroups
        threads={[
          { id: "a", title: "Same", project_id: "p1", updated_at: ago(MIN) },
          { id: "b", title: "Same", updated_at: ago(2 * MIN) },
        ]}
        projects={PROJECTS}
        onOpen={() => {}}
        lead={{ projectId: "p1", name: "Harbor Street Cafe" }}
        foldOthers
      />,
    );
    expect(screen.getByTestId("thread-row-a")).toBeTruthy();
    expect(screen.queryByTestId("thread-twin-a")).toBeNull();
    fireEvent.click(screen.getByTestId("thread-group-toggle-none"));
    expect(screen.getByTestId("thread-twin-a")).toBeTruthy();
    expect(screen.getByTestId("thread-twin-b")).toBeTruthy();
  });
});

/* ============================================ ThreadGroups' new props */

describe("ThreadGroups: lead, folding (pure)", () => {
  it("the lead's pinned chat stays in the lead group, first and marked; another project's pin goes to Pinned", () => {
    render(
      <ThreadGroups
        threads={[
          { id: "a", title: "Newer", project_id: "p1", updated_at: ago(MIN) },
          { id: "b", title: "Pinned here", project_id: "p1", updated_at: ago(9 * MIN) },
          { id: "c", title: "Pinned elsewhere", project_id: "p2", updated_at: ago(MIN) },
        ]}
        projects={PROJECTS}
        onOpen={() => {}}
        pinnedIds={["b", "c"]}
        lead={{ projectId: "p1", name: "Harbor Street Cafe" }}
        foldOthers
      />,
    );
    const order = [...document.querySelectorAll("section")].map((s) => s.getAttribute("data-testid"));
    expect(order).toEqual(["thread-group-p:p1", "thread-group-pinned"]);
    const lead = screen.getByTestId("thread-group-p:p1");
    const rows = [...lead.querySelectorAll("button[data-thread-row]")].map((b) => b.getAttribute("data-testid"));
    expect(rows).toEqual(["thread-row-b", "thread-row-a"]);
    expect(within(screen.getByTestId("thread-row-b")).getByLabelText("Pinned")).toBeTruthy();
    expect(screen.getByTestId("thread-group-pinned").getAttribute("data-folded")).toBe("true");
  });

  it("the lead group is named before the project list answers", () => {
    render(
      <ThreadGroups
        threads={[{ id: "a", title: "Mine", project_id: "p9", updated_at: ago(MIN) }]}
        projects={[]}
        onOpen={() => {}}
        lead={{ projectId: "p9", name: "This project" }}
        foldOthers
      />,
    );
    expect(screen.getByRole("heading", { name: "This project" })).toBeTruthy();
    expect(screen.queryByTestId("thread-group-none")).toBeNull();
  });

  it("the open chat's group never starts folded; forceOpen opens them all", () => {
    const rows = [
      { id: "a", title: "Lead chat", project_id: "p1", updated_at: ago(MIN) },
      { id: "b", title: "Open elsewhere", project_id: "p2", updated_at: ago(MIN) },
      { id: "c", title: "Loose", updated_at: ago(MIN) },
    ];
    const lead = { projectId: "p1", name: "Harbor Street Cafe" };
    const { rerender } = render(
      <ThreadGroups threads={rows} projects={PROJECTS} onOpen={() => {}} activeId="b" lead={lead} foldOthers />,
    );
    expect(screen.getByTestId("thread-row-b")).toBeTruthy();
    expect(screen.queryByTestId("thread-row-c")).toBeNull();
    rerender(
      <ThreadGroups threads={rows} projects={PROJECTS} onOpen={() => {}} activeId="b" lead={lead} foldOthers forceOpen />,
    );
    expect(screen.getByTestId("thread-row-c")).toBeTruthy();
  });

  it("groupStatus: waiting beats working beats new; nothing lit is idle", () => {
    const t = [{ id: "a" }, { id: "b" }, { id: "c" }];
    expect(groupStatus(t, { a: "unread", b: "running", c: "waiting" })).toBe("waiting");
    expect(groupStatus(t, { a: "unread", b: "running" })).toBe("running");
    expect(groupStatus(t, { a: "unread" })).toBe("unread");
    expect(groupStatus(t, {})).toBe("idle");
  });
});

/* ===================================== (2) the sidebar list's search */

describe("off /chat the sidebar list has a quiet search", () => {
  it("filters in place; no match says so; Escape clears it and stops there", async () => {
    H.nav.pathname = "/usage";
    render(<AppSidebar />);
    const list = await screen.findByTestId("sidebar-recent-chats");
    await within(list).findByTitle("Lease options");
    const box = within(list).getByLabelText("Search chats") as HTMLInputElement;
    fireEvent.change(box, { target: { value: "LEASE" } });
    expect(within(list).getByTitle("Lease options")).toBeTruthy();
    expect(within(list).queryByTitle("Q3 revenue by location")).toBeNull();
    fireEvent.change(box, { target: { value: "zzz" } });
    expect(within(list).getByText(/No chats match/)).toBeTruthy();
    const drawerEscape = vi.fn();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") drawerEscape();
    };
    window.addEventListener("keydown", onKey);
    try {
      fireEvent.keyDown(box, { key: "Escape" });
      expect(box.value).toBe("");
      expect(drawerEscape).not.toHaveBeenCalled();
      expect(within(list).getByTitle("Q3 revenue by location")).toBeTruthy();
      // CONTROL: an empty box lets Escape through to the drawer.
      fireEvent.keyDown(box, { key: "Escape" });
      expect(drawerEscape).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("keydown", onKey);
    }
  });

  it("a search shows every match, past the group's first five", async () => {
    H.nav.pathname = "/usage";
    H.responses["/chat/threads"] = {
      threads: Array.from({ length: 8 }, (_, i) => ({
        id: `l${i}`,
        title: `Loose chat ${i}`,
        updated_at: ago((i + 1) * MIN),
      })),
    };
    render(<AppSidebar />);
    const list = await screen.findByTestId("sidebar-recent-chats");
    await within(list).findByTitle("Loose chat 0");
    expect(within(list).queryByTitle("Loose chat 7")).toBeNull();
    fireEvent.change(within(list).getByLabelText("Search chats"), { target: { value: "loose" } });
    expect(within(list).getByTitle("Loose chat 7")).toBeTruthy();
  });
});

/* ============================== (4) the phone drawer is ONE column */

describe("the phone drawer scrolls as one column", () => {
  async function openDrawer() {
    await act(async () => {
      window.dispatchEvent(new CustomEvent("ij:toggle-nav"));
    });
    return screen.findByRole("dialog", { name: "Navigation" });
  }

  it("on /chat the drawer's slot flows as a column and does not fill-and-scroll", async () => {
    setWide(false);
    render(<NavDrawer />);
    await openDrawer();
    const slot = document.getElementById("ij-drawer-chat-slot")!;
    expect(slot.getAttribute("data-flow")).toBe("column");
    const cls = (slot.getAttribute("class") ?? "").split(/\s+/);
    expect(cls).not.toContain("flex-1");
    expect(cls).toContain("shrink-0");
  });

  it("CONTROL: the wide rail's slot keeps filling the space above Projects", async () => {
    render(<AppSidebar />);
    await waitFor(() => expect(document.getElementById("ij-sidebar-chat-slot")).toBeTruthy());
    const slot = document.getElementById("ij-sidebar-chat-slot")!;
    expect(slot.getAttribute("data-flow")).toBeNull();
    expect((slot.getAttribute("class") ?? "").split(/\s+/)).toContain("flex-1");
  });

  it("in a column slot the chat page's list has no scroll box of its own; in the rail's slot it keeps one", async () => {
    const slot = document.createElement("div");
    slot.id = "ij-drawer-chat-slot";
    slot.setAttribute("data-flow", "column");
    document.body.appendChild(slot);
    claimChatSlot(slot, () => {});
    try {
      render(<ChatPage />);
      const rail = await screen.findByTestId("chat-thread-rail");
      expect(slot.contains(rail)).toBe(true);
      await within(rail).findByTitle("Lease options");
      expect(rail.querySelector(".overflow-y-auto")).toBeNull();
      expect((rail.getAttribute("class") ?? "").split(/\s+/)).not.toContain("flex-1");
    } finally {
      setChatSlot(null);
      slot.remove();
    }
    cleanup();
    const wide = document.createElement("div");
    wide.id = "ij-sidebar-chat-slot";
    document.body.appendChild(wide);
    claimChatSlot(wide);
    try {
      render(<ChatPage />);
      const rail = await screen.findByTestId("chat-thread-rail");
      await within(rail).findByTitle("Lease options");
      expect(rail.querySelector(".overflow-y-auto")).not.toBeNull();
    } finally {
      setChatSlot(null);
      wide.remove();
    }
  });
});
