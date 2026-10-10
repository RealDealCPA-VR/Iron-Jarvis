/**
 * v1.327.0 (calm chat W2-1) — the chat page's thread list shows which chats
 * are running, waiting on you or unread, grouped under projects.
 *
 * Wiring pinned here (the pure pieces are pinned in thread-groups-v1327):
 *  - EVERY turn names the open saved chat: `thread_id` on the /chat/stream
 *    body AND on the POST /chat fallback (the daemon lights that chat as
 *    running / waiting); a new unsaved chat sends none;
 *  - the rail is ThreadGroups: project headings, a dot per status (running /
 *    waiting from the daemon's row, unread from this browser's stamps), the
 *    ⋯ menu and the search kept;
 *  - opening a chat stamps it seen (and leaving it does too), so the open
 *    chat never reads unread and a chat just read does not light up after;
 *  - while any row is running or waiting the list is re-read every
 *    THREAD_POLL_MS; none live = no timer; hidden window = no timer (one
 *    catch-up read on return); unmount = no timer.
 *
 * Harness: the v1.315.0 chat mocks (ChatPage rendered for real; an unmocked
 * GET answers {}), with the stream's bodies recorded and a stream that can
 * say "endpoint absent" (404) so the page takes its POST lane.
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
    api: {
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      posts: [] as { path: string; body: unknown }[],
      responses: {} as Record<string, unknown>,
    },
    stream: {
      /** "ok" answers; "absent" is an old daemon (404) → the POST lane. */
      mode: "ok" as "ok" | "absent",
      bodies: [] as Record<string, unknown>[],
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
  post: async (path: string, body?: unknown) => {
    H.api.posts.push({ path, body });
    if (path === "/chat") return { reply: "Posted answer." };
    return {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
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
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: (body: Record<string, unknown>) => {
      H.stream.bodies.push(body);
      if (H.stream.mode === "absent") {
        return Promise.reject(new H.FakeStreamError("Not Found", 404));
      }
      return Promise.resolve({ reply: "Streamed answer.", tools_used: [] });
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
vi.mock("@/components/project/ProjectSurfaces", () => ({
  ProjectSurface: ({ view }: { view: string }) => <div data-testid="surface-stub">surface:{view}</div>,
}));

import ChatPage from "@/app/chat/page";
import { __resetApiCache } from "@/lib/apiCache";
import { LAST_VIEWED_KEY } from "@/lib/threadStatus";
import { THREAD_POLL_MS, useThreadListPoll } from "@/lib/threadListPoll";
import { NEW_CHAT_EVENT } from "@/lib/sidebarSlot";

/* ------------------------------------------------------------------ data */

const PROJECT = { id: "p1", name: "Harbor Street Cafe" };
/** This browser started keeping stamps here: older changes are not unread. */
const SINCE = Date.UTC(2026, 9, 5, 0, 0, 0);
const OLD = "2026-10-01T10:00:00"; // the daemon's naive UTC, before SINCE
const RECENT = "2026-10-08T10:00:00"; // after SINCE

const RUNNING = { id: "tr", title: "Running chat", project_id: "p1", updated_at: OLD, messages: 2, running: true };
const WAITING = { id: "tw", title: "Waiting chat", project_id: "p1", updated_at: OLD, messages: 2, waiting: true };
const CHANGED = { id: "tn", title: "Changed chat", updated_at: RECENT, messages: 2 };
const QUIET = { id: "to", title: "Quiet chat", updated_at: OLD, messages: 2 };

const detail = (row: { id: string; title: string; updated_at: string }) => ({
  id: row.id,
  title: row.title,
  updated_at: row.updated_at,
  messages: [
    { role: "user", content: `question in ${row.title}` },
    { role: "assistant", content: `answer in ${row.title}` },
  ],
});

/* --------------------------------------------------------------- helpers */

async function settle(): Promise<void> {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const listReads = () => H.api.gets.filter((p) => p === "/chat/threads").length;
const dot = (id: string) => screen.getByTestId(`thread-dot-${id}`).getAttribute("data-status");
const composerBox = async () =>
  (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;

async function send(text: string) {
  const box = await composerBox();
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
}

function setVisibility(state: "visible" | "hidden") {
  const hidden = state === "hidden";
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  __resetApiCache();
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.api.posts.length = 0;
  H.stream.mode = "ok";
  H.stream.bodies.length = 0;
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [RUNNING, WAITING, CHANGED, QUIET] },
    "/chat/threads/tn": detail(CHANGED),
    "/chat/threads/to": detail(QUIET),
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
  window.localStorage.setItem(LAST_VIEWED_KEY, JSON.stringify({ v: 1, since: SINCE, seen: {} }));
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
  setVisibility("visible");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/* ============================================================ thread_id */

describe("every turn names the open saved chat (thread_id)", () => {
  it("the stream lane sends the open chat's id", async () => {
    window.history.replaceState({}, "", "/chat?thread=to");
    render(<ChatPage />);
    await screen.findByText("answer in Quiet chat");
    await send("one more thing");
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    expect(H.stream.bodies[0].thread_id).toBe("to");
  });

  it("the POST lane (an older daemon with no /chat/stream) sends it too", async () => {
    H.stream.mode = "absent";
    window.history.replaceState({}, "", "/chat?thread=to");
    render(<ChatPage />);
    await screen.findByText("answer in Quiet chat");
    await send("one more thing");
    await screen.findByText("Posted answer.");
    const posted = H.api.posts.filter((p) => p.path === "/chat");
    expect(posted).toHaveLength(1);
    expect((posted[0].body as Record<string, unknown>).thread_id).toBe("to");
  });

  it("CONTROL: a new unsaved chat sends no thread_id", async () => {
    render(<ChatPage />);
    await composerBox();
    await settle();
    await send("first question");
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    expect("thread_id" in H.stream.bodies[0]).toBe(false);
  });
});

/* ================================================================= dots */

describe("the rail is grouped and shows each chat's status", () => {
  it("running, waiting and unread rows carry their dot; an old quiet chat has none", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Running chat");
    expect(dot("tr")).toBe("running");
    expect(dot("tw")).toBe("waiting");
    expect(dot("tn")).toBe("unread");
    expect(dot("to")).toBe("idle");
    // Grouped under the project, chats with none last; the ⋯ stays per row.
    const groups = within(rail).getAllByRole("region").map((s) => s.getAttribute("data-testid"));
    expect(groups).toEqual(["thread-group-p:p1", "thread-group-none"]);
    expect(within(rail).getByRole("heading", { name: PROJECT.name })).toBeTruthy();
    expect(within(rail).getAllByRole("button", { name: /^Options for / })).toHaveLength(4);
    // The words, not only the colour.
    expect(screen.getByTestId("thread-row-tw").textContent).toContain("Waiting on you");
  });

  it("opening a chat stamps it seen: the open chat is never unread, nor is it after you leave", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    expect(await within(rail).findByTitle("Changed chat")).toBeTruthy();
    expect(dot("tn")).toBe("unread");
    fireEvent.click(within(rail).getByTitle("Changed chat"));
    await screen.findByText("answer in Changed chat");
    // The stamp is WRITTEN at open (not only implied by "it is on screen").
    const seen = JSON.parse(window.localStorage.getItem(LAST_VIEWED_KEY) || "{}").seen ?? {};
    expect(typeof seen.tn).toBe("number");
    expect(dot("tn")).toBe("idle");
    // Leave it: still read.
    await act(async () => {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    });
    await waitFor(() => expect(screen.queryByText("answer in Changed chat")).toBeNull());
    expect(dot("tn")).toBe("idle");
  });

  it("the open chat stays read when it changes while you are in it (your own save moves its time)", async () => {
    window.history.replaceState({}, "", "/chat?thread=to");
    render(<ChatPage />);
    await screen.findByText("answer in Quiet chat");
    // The save after this turn re-lists, and the daemon now dates the chat
    // well after the open stamp.
    H.api.responses["/chat/threads"] = {
      threads: [RUNNING, WAITING, CHANGED, { ...QUIET, updated_at: "2026-12-01T00:00:00" }],
    };
    const before = H.api.gets.filter((p) => p === "/chat/threads").length;
    await send("one more thing");
    await screen.findByText("Streamed answer.");
    await waitFor(() => expect(listReads()).toBeGreaterThan(before));
    await settle();
    expect(screen.getByTestId("thread-row-to").querySelector("time")?.getAttribute("datetime")).toBe(
      "2026-12-01T00:00:00",
    );
    expect(dot("to")).toBe("idle");
  });

  it("leaving a chat stamps it again (its own messages moved its time while it was open)", async () => {
    window.history.replaceState({}, "", "/chat?thread=to");
    render(<ChatPage />);
    await screen.findByText("answer in Quiet chat");
    // Pretend the open stamp is old: only the LEAVE can renew it.
    window.localStorage.setItem(LAST_VIEWED_KEY, JSON.stringify({ v: 1, since: SINCE, seen: { to: 1 } }));
    await act(async () => {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    });
    await waitFor(() => expect(screen.queryByText("answer in Quiet chat")).toBeNull());
    const seen = JSON.parse(window.localStorage.getItem(LAST_VIEWED_KEY) || "{}").seen ?? {};
    expect(seen.to).toBeGreaterThan(SINCE);
  });

  it("a search narrows the grouped list; no match says so", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Running chat");
    fireEvent.change(within(rail).getByLabelText("Search chats"), { target: { value: "waiting" } });
    expect(within(rail).getByTitle("Waiting chat")).toBeTruthy();
    expect(within(rail).queryByTitle("Running chat")).toBeNull();
    fireEvent.change(within(rail).getByLabelText("Search chats"), { target: { value: "zzz" } });
    expect(within(rail).getByText(/No chats match/)).toBeTruthy();
  });
});

/* ================================================ pins, scope, row slots */

describe("pins, a project-scoped rail and the row slots", () => {
  it("a pinned chat sits first, under 'Pinned', and not again under its project", async () => {
    window.localStorage.setItem("ij_chat_pinned", JSON.stringify(["tw"]));
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Waiting chat");
    const groups = within(rail).getAllByRole("region").map((s) => s.getAttribute("data-testid"));
    expect(groups).toEqual(["thread-group-pinned", "thread-group-p:p1", "thread-group-none"]);
    const pinned = screen.getByTestId("thread-group-pinned");
    expect(within(pinned).getByRole("heading").textContent).toBe("Pinned");
    expect(within(pinned).getByTitle("Waiting chat")).toBeTruthy();
    expect(within(screen.getByTestId("thread-group-p:p1")).queryByTitle("Waiting chat")).toBeNull();
  });

  it("a rail scoped to one project is one plain list: no headings, no 'Show more', every chat", async () => {
    window.localStorage.setItem("ij_chat_project", PROJECT.id);
    const many = Array.from({ length: 8 }, (_, i) => ({
      id: `m${i}`,
      title: `Project chat ${i}`,
      project_id: "p1",
      updated_at: OLD,
      messages: 2,
    }));
    H.api.responses["/chat/threads?project_id=p1"] = { threads: many };
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    // v1.329.0 (calm chat W5 G3): a project chat opens on the grouped list
    // (that project's group first); the plain one-project list is now the
    // "Only <project>" filter, one press away.
    fireEvent.click(await within(rail).findByRole("button", { name: `Only ${PROJECT.name}` }));
    expect(await within(rail).findByText(/Threads in Harbor Street Cafe/)).toBeTruthy();
    await within(rail).findByTitle("Project chat 7");
    // (The rail's own "Chat" title is an h1; the LIST has no headings.)
    expect(within(within(rail).getByTestId("thread-groups")).queryAllByRole("heading")).toHaveLength(0);
    expect(within(rail).queryByText("Show more")).toBeNull();
    expect(within(rail).getAllByRole("button", { name: /^Options for Project chat/ })).toHaveLength(8);
  });

  it("CONTROL: the unscoped list cuts a long group at five, with 'Show more'", async () => {
    const many = Array.from({ length: 8 }, (_, i) => ({
      id: `m${i}`,
      title: `Loose chat ${i}`,
      updated_at: OLD,
      messages: 2,
    }));
    H.api.responses["/chat/threads"] = { threads: many };
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Loose chat 0");
    expect(within(rail).queryByTitle("Loose chat 7")).toBeNull();
    fireEvent.click(within(rail).getByText("Show more"));
    expect(within(rail).getByTitle("Loose chat 7")).toBeTruthy();
  });

  it("Rename puts the box in the row's place; Enter saves the new title", async () => {
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Quiet chat");
    fireEvent.click(within(rail).getByRole("button", { name: "Options for Quiet chat" }));
    const menu = await screen.findByRole("menu", { name: /^Options for / });
    fireEvent.click(within(menu).getByText(/Rename/));
    const box = (await within(rail).findByLabelText("Rename chat")) as HTMLInputElement;
    expect(screen.queryByTestId("thread-row-to")).toBeNull();
    fireEvent.change(box, { target: { value: "Renamed chat" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() =>
      expect(H.api.puts.some((p) => p.path === "/chat/threads/to" && p.body.title === "Renamed chat")).toBe(true),
    );
  });
});

describe("ThreadGroups' row slots (pure)", () => {
  const rows = [
    { id: "a", title: "Alpha", project_id: "p1", updated_at: OLD },
    { id: "b", title: "Bravo", updated_at: OLD },
  ];

  it("the action sits BESIDE the row button, the age makes room for it on a mouse, the badge rides the row", async () => {
    const { default: ThreadGroups } = await import("@/components/chat/ThreadGroups");
    render(
      <ThreadGroups
        threads={rows}
        projects={[PROJECT]}
        onOpen={() => {}}
        rowAction={(t) => <button type="button">act {t.id}</button>}
        rowBadge={(t) => (t.id === "b" ? <span>Telegram</span> : null)}
      />,
    );
    const row = screen.getByTestId("thread-row-a");
    const act = screen.getByRole("button", { name: "act a" });
    expect(row.contains(act)).toBe(false);
    expect(row.parentElement?.contains(act)).toBe(true);
    expect(row.className).toContain("pr-9");
    expect(row.querySelector("time")?.className).toContain("[@media(hover:hover)]:group-hover/thread:opacity-0");
    expect(screen.getByTestId("thread-row-b").textContent).toContain("Telegram");
  });

  it("CONTROL: no action, no reserved room and no hover-hidden age", async () => {
    const { default: ThreadGroups } = await import("@/components/chat/ThreadGroups");
    render(<ThreadGroups threads={rows} projects={[PROJECT]} onOpen={() => {}} />);
    const row = screen.getByTestId("thread-row-a");
    expect(row.className).not.toContain("pr-9");
    expect(row.querySelector("time")?.className).not.toContain("opacity-0");
  });

  it("headings off: one plain list, pinned rows first and marked", async () => {
    const { default: ThreadGroups } = await import("@/components/chat/ThreadGroups");
    render(
      <ThreadGroups threads={rows} projects={[PROJECT]} onOpen={() => {}} headings={false} pinnedIds={["b"]} />,
    );
    expect(screen.queryAllByRole("heading")).toHaveLength(0);
    const order = Array.from(document.querySelectorAll("button[data-thread-row]")).map((b) =>
      b.getAttribute("data-testid"),
    );
    expect(order).toEqual(["thread-row-b", "thread-row-a"]);
    expect(within(screen.getByTestId("thread-row-b")).getByLabelText("Pinned")).toBeTruthy();
    expect(within(screen.getByTestId("thread-row-a")).queryByLabelText("Pinned")).toBeNull();
  });
});

/* ============================================================== polling */

describe("the list is re-read while a chat is running or waiting", () => {
  it("polls every THREAD_POLL_MS while a row is live, and stops once none is", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Running chat");
    await settle();
    const start = listReads();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THREAD_POLL_MS);
    });
    await waitFor(() => expect(listReads()).toBe(start + 1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THREAD_POLL_MS);
    });
    await waitFor(() => expect(listReads()).toBe(start + 2));
    // The chats finish: the next read shows none live, and the timer ends.
    H.api.responses["/chat/threads"] = {
      threads: [{ ...RUNNING, running: false }, { ...WAITING, waiting: false }, CHANGED, QUIET],
    };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THREAD_POLL_MS);
    });
    await waitFor(() => expect(dot("tr")).toBe("idle"));
    const after = listReads();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THREAD_POLL_MS * 4);
    });
    expect(listReads()).toBe(after);
  });

  it("CONTROL: with no live row nothing polls", async () => {
    H.api.responses["/chat/threads"] = { threads: [CHANGED, QUIET] };
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Quiet chat");
    await settle();
    const start = listReads();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THREAD_POLL_MS * 4);
    });
    expect(listReads()).toBe(start);
  });
});

/* ================================================ the poll hook's edges */

function Poller({ threads, refresh }: { threads: { id: string; running?: boolean; waiting?: boolean }[]; refresh: () => void }) {
  useThreadListPoll(threads, refresh);
  return null;
}

describe("useThreadListPoll: hidden, unmount, live flags", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("runs while a row is running or waiting", () => {
    const refresh = vi.fn();
    const { rerender } = render(<Poller threads={[{ id: "a", running: true }]} refresh={refresh} />);
    act(() => void vi.advanceTimersByTime(THREAD_POLL_MS * 3));
    expect(refresh).toHaveBeenCalledTimes(3);
    rerender(<Poller threads={[{ id: "a", waiting: true }]} refresh={refresh} />);
    act(() => void vi.advanceTimersByTime(THREAD_POLL_MS));
    expect(refresh).toHaveBeenCalledTimes(4);
    // None live: the timer is gone.
    rerender(<Poller threads={[{ id: "a" }, { id: "b", running: false }]} refresh={refresh} />);
    act(() => void vi.advanceTimersByTime(THREAD_POLL_MS * 5));
    expect(refresh).toHaveBeenCalledTimes(4);
  });

  it("stops while the window is hidden and reads once when it shows again", () => {
    const refresh = vi.fn();
    render(<Poller threads={[{ id: "a", running: true }]} refresh={refresh} />);
    act(() => setVisibility("hidden"));
    act(() => void vi.advanceTimersByTime(THREAD_POLL_MS * 5));
    expect(refresh).toHaveBeenCalledTimes(0);
    act(() => setVisibility("visible"));
    expect(refresh).toHaveBeenCalledTimes(1);
    act(() => void vi.advanceTimersByTime(THREAD_POLL_MS));
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("stops on unmount", () => {
    const refresh = vi.fn();
    const { unmount } = render(<Poller threads={[{ id: "a", running: true }]} refresh={refresh} />);
    act(() => void vi.advanceTimersByTime(THREAD_POLL_MS));
    expect(refresh).toHaveBeenCalledTimes(1);
    unmount();
    act(() => void vi.advanceTimersByTime(THREAD_POLL_MS * 5));
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
