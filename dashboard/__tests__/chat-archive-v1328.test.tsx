/**
 * v1.328.0 (calm chat W3-3) — archive a chat from the chat list, and stop it
 * first when it is still working.
 *
 * Wiring pinned here (the dialog itself is pinned in archive-chat-v1328, the
 * daemon in tests/test_chat_archive_v1328.py):
 *  - the ⋯ menu has Archive; nothing running = archived at once (POST
 *    /chat/threads/{id}/archive with {}), the row leaves the list and a quiet
 *    "Archived (N)" link appears at its foot;
 *  - archiving the OPEN chat leaves it for a new chat, like Delete;
 *  - the daemon's 409 opens ArchiveChatDialog with ITS list of what is
 *    running and nothing is archived; "Stop and archive" re-sends with
 *    {stop: true}; Cancel sends nothing more; a `still_running` answer shows
 *    the daemon's note, never a claim that everything stopped;
 *  - "Archived (N)" swaps the list for GET /chat/threads?archived=only; a
 *    row's ⋯ there offers Unarchive and Delete only; Unarchive brings the
 *    chat back to the list; pressing an archived row brings it back AND
 *    opens it; "Chats" goes back;
 *  - no archived chats = no link (control).
 *
 * Harness: the v1.327.0 chat mocks (ChatPage rendered for real), with a tiny
 * daemon model behind both the mocked `get`/`post` and the archive request
 * (which lib/archiveChat makes with fetch, so the 409's list can be read).
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
  type Row = { id: string; title: string; updated_at: string; messages: number; project_id?: string | null };
  return {
    FakeApiError,
    FakeStreamError,
    /** The daemon: every chat, and which are archived. */
    db: {
      rows: [] as Row[],
      archived: new Set<string>(),
      /** What the chat is running (the 409's list); [] = idle. */
      running: {} as Record<string, string[]>,
      /** What a stop could not reach (still_running). */
      unstoppable: {} as Record<string, string[]>,
    },
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    dels: [] as string[],
    archives: [] as { id: string; body: Record<string, unknown> }[],
  };
});

const detail = (id: string) => {
  const r = H.db.rows.find((x) => x.id === id)!;
  return {
    id: r.id,
    title: r.title,
    updated_at: r.updated_at,
    messages: [
      { role: "user", content: `question in ${r.title}` },
      { role: "assistant", content: `answer in ${r.title}` },
    ],
  };
};

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    H.gets.push(path);
    if (path === "/chat/threads") {
      return { threads: H.db.rows.filter((r) => !H.db.archived.has(r.id)) };
    }
    if (path === "/chat/threads?archived=only") {
      return { threads: H.db.rows.filter((r) => H.db.archived.has(r.id)) };
    }
    const m = /^\/chat\/threads\/([^/?]+)$/.exec(path);
    if (m && H.db.rows.some((r) => r.id === m[1])) return detail(m[1]);
    if (path === "/models") return { models: [] };
    if (path === "/projects") return { projects: [] };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    const m = /^\/chat\/threads\/([^/]+)\/unarchive$/.exec(path);
    if (m) {
      H.db.archived.delete(decodeURIComponent(m[1]));
      return { id: m[1], archived: false, archived_at: null };
    }
    return {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t-new", title: "t", updated_at: "2026-10-09T10:00:00" };
  },
  patch: async () => ({}),
  del: async (path: string) => {
    H.dels.push(path);
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    if (m) H.db.rows = H.db.rows.filter((r) => r.id !== m[1]);
    return undefined;
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
    run: () => Promise.resolve({ reply: "Streamed answer.", tools_used: [] }),
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

/* ------------------------------------------------------------------ data */

const T1 = { id: "t1", title: "Quarterly notes", updated_at: "2026-10-08T10:00:00", messages: 2 };
const T2 = { id: "t2", title: "Harbor menu", updated_at: "2026-10-07T10:00:00", messages: 2 };
const T3 = { id: "t3", title: "Old receipts", updated_at: "2026-10-01T10:00:00", messages: 2 };

/** The daemon's POST /chat/threads/{id}/archive, served to lib/archiveChat's fetch. */
function fakeArchiveFetch(url: string, init?: RequestInit) {
  const m = /\/chat\/threads\/([^/]+)\/archive$/.exec(url);
  if (!m) return Promise.resolve({ status: 404, ok: false, statusText: "Not Found", json: async () => ({}) });
  const id = decodeURIComponent(m[1]);
  const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
  H.archives.push({ id, body });
  const running = H.db.running[id] ?? [];
  if (running.length && !body.stop) {
    return Promise.resolve({
      status: 409,
      ok: false,
      statusText: "Conflict",
      json: async () => ({ detail: "This chat is still working.", running }),
    });
  }
  const still = running.length ? H.db.unstoppable[id] ?? [] : [];
  H.db.archived.add(id);
  return Promise.resolve({
    status: 200,
    ok: true,
    statusText: "OK",
    json: async () => ({
      id,
      archived: true,
      stopped: running.filter((r) => !still.includes(r)),
      still_running: still,
      ...(still.length
        ? { note: `Archived. This could not be stopped from here and will finish on its own: ${still.join(", ")}.` }
        : {}),
    }),
  });
}

/* --------------------------------------------------------------- helpers */

async function settle(): Promise<void> {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const rail = () => screen.getByTestId("chat-thread-rail");

async function openMenu(title: string) {
  fireEvent.click(await within(rail()).findByLabelText(`Options for ${title}`));
  return screen.findByRole("menu", { name: `Options for ${title}` });
}

beforeEach(() => {
  __resetApiCache();
  H.db.rows = [{ ...T1 }, { ...T2 }, { ...T3 }];
  H.db.archived = new Set();
  H.db.running = {};
  H.db.unstoppable = {};
  H.gets.length = 0;
  H.posts.length = 0;
  H.dels.length = 0;
  H.archives.length = 0;
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => fakeArchiveFetch(String(url), init)));
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/* ============================================================== archive */

describe("Archive in the ⋯ menu", () => {
  it("archives an idle chat at once: the row leaves the list and 'Archived (1)' appears", async () => {
    render(<ChatPage />);
    const menu = await openMenu("Quarterly notes");
    fireEvent.click(within(menu).getByTestId("thread-menu-archive"));
    await waitFor(() => expect(within(rail()).queryByText("Quarterly notes")).toBeNull());
    expect(H.archives).toEqual([{ id: "t1", body: {} }]);
    expect(H.db.archived.has("t1")).toBe(true);
    await waitFor(() =>
      expect(within(rail()).getByTestId("thread-archived-link").textContent).toContain("Archived (1)"),
    );
    // Nothing was running, so nothing was asked.
    expect(screen.queryByTestId("archive-chat-dialog")).toBeNull();
    // The other chats stay.
    expect(within(rail()).getByText("Harbor menu")).toBeTruthy();
  });

  it("archiving the OPEN chat leaves it for a new chat, like Delete", async () => {
    window.history.replaceState({}, "", "/chat?thread=t1");
    render(<ChatPage />);
    await screen.findByText("answer in Quarterly notes");
    const menu = await openMenu("Quarterly notes");
    fireEvent.click(within(menu).getByTestId("thread-menu-archive"));
    await waitFor(() => expect(screen.queryByText("answer in Quarterly notes")).toBeNull());
    expect(H.db.archived.has("t1")).toBe(true);
  });

  it("CONTROL: archiving ANOTHER chat keeps the open one on screen", async () => {
    window.history.replaceState({}, "", "/chat?thread=t1");
    render(<ChatPage />);
    await screen.findByText("answer in Quarterly notes");
    const menu = await openMenu("Harbor menu");
    fireEvent.click(within(menu).getByTestId("thread-menu-archive"));
    await waitFor(() => expect(within(rail()).queryByText("Harbor menu")).toBeNull());
    expect(screen.getByText("answer in Quarterly notes")).toBeTruthy();
  });

  it("no archived chats = no 'Archived' link", async () => {
    render(<ChatPage />);
    await within(rail()).findByText("Quarterly notes");
    await settle();
    expect(H.gets).toContain("/chat/threads?archived=only");
    expect(within(rail()).queryByTestId("thread-archived-link")).toBeNull();
  });
});

/* ================================================== still working → stop */

describe("a chat that is still working asks first", () => {
  it("the 409 opens the dialog with the daemon's list, and nothing is archived yet", async () => {
    H.db.running.t1 = ["a reply in progress", "a question waiting for you"];
    render(<ChatPage />);
    const menu = await openMenu("Quarterly notes");
    fireEvent.click(within(menu).getByTestId("thread-menu-archive"));
    const dialog = await screen.findByTestId("archive-chat-dialog");
    const items = within(dialog)
      .getAllByTestId("archive-chat-running-item")
      .map((li) => li.textContent);
    expect(items).toEqual(["a reply in progress", "a question waiting for you"]);
    expect(within(dialog).getByTestId("archive-chat-heading").textContent).toBe("Archive 'Quarterly notes'?");
    expect(H.db.archived.has("t1")).toBe(false);
    expect(within(rail()).getByText("Quarterly notes")).toBeTruthy();
  });

  it("'Stop and archive' re-sends with {stop: true}, then the chat leaves the list", async () => {
    H.db.running.t1 = ["a reply in progress"];
    render(<ChatPage />);
    const menu = await openMenu("Quarterly notes");
    fireEvent.click(within(menu).getByTestId("thread-menu-archive"));
    const confirm = await screen.findByTestId("archive-chat-confirm");
    expect(confirm.textContent).toBe("Stop and archive");
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.queryByTestId("archive-chat-dialog")).toBeNull());
    expect(H.archives).toEqual([
      { id: "t1", body: {} },
      { id: "t1", body: { stop: true } },
    ]);
    await waitFor(() => expect(within(rail()).queryByText("Quarterly notes")).toBeNull());
    // Everything stopped: no note.
    expect(screen.queryByTestId("thread-archive-note")).toBeNull();
  });

  it("work that could not be stopped is NAMED in the daemon's note, never called stopped", async () => {
    H.db.running.t1 = ["a reply in progress"];
    H.db.unstoppable.t1 = ["a reply in progress"];
    render(<ChatPage />);
    const menu = await openMenu("Quarterly notes");
    fireEvent.click(within(menu).getByTestId("thread-menu-archive"));
    fireEvent.click(await screen.findByTestId("archive-chat-confirm"));
    await waitFor(() =>
      expect(screen.getByTestId("thread-archive-note").textContent).toContain(
        "could not be stopped from here and will finish on its own: a reply in progress",
      ),
    );
    expect(H.db.archived.has("t1")).toBe(true);
  });

  it("Cancel sends nothing more and keeps the chat", async () => {
    H.db.running.t1 = ["a reply in progress"];
    render(<ChatPage />);
    const menu = await openMenu("Quarterly notes");
    fireEvent.click(within(menu).getByTestId("thread-menu-archive"));
    fireEvent.click(await screen.findByTestId("archive-chat-cancel"));
    await waitFor(() => expect(screen.queryByTestId("archive-chat-dialog")).toBeNull());
    await settle();
    expect(H.archives).toEqual([{ id: "t1", body: {} }]);
    expect(H.db.archived.has("t1")).toBe(false);
    expect(within(rail()).getByText("Quarterly notes")).toBeTruthy();
  });
});

/* ======================================================= archived view */

describe("the Archived view", () => {
  beforeEach(() => {
    H.db.archived = new Set(["t3"]);
  });

  it("'Archived (1)' lists the archived chats in the list's place; 'Chats' goes back", async () => {
    render(<ChatPage />);
    const link = await within(rail()).findByTestId("thread-archived-link");
    expect(link.textContent).toContain("Archived (1)");
    // The default list leaves archived chats out.
    expect(within(rail()).queryByText("Old receipts")).toBeNull();
    fireEvent.click(link);
    const view = await within(rail()).findByTestId("thread-archived-view");
    expect(within(view).getByText("Old receipts")).toBeTruthy();
    expect(within(view).queryByText("Quarterly notes")).toBeNull();
    fireEvent.click(within(view).getByTestId("thread-archived-back"));
    await waitFor(() => expect(within(rail()).queryByTestId("thread-archived-view")).toBeNull());
    expect(within(rail()).getByText("Quarterly notes")).toBeTruthy();
  });

  it("an archived row's ⋯ offers Unarchive and Delete only", async () => {
    render(<ChatPage />);
    fireEvent.click(await within(rail()).findByTestId("thread-archived-link"));
    await within(rail()).findByTestId("thread-archived-view");
    const menu = await openMenu("Old receipts");
    expect(within(menu).getByTestId("thread-menu-unarchive")).toBeTruthy();
    expect(within(menu).getByRole("menuitem", { name: /Delete chat/ })).toBeTruthy();
    expect(within(menu).queryByTestId("thread-menu-archive")).toBeNull();
    expect(within(menu).queryByRole("menuitem", { name: /Rename/ })).toBeNull();
  });

  it("Unarchive brings the chat back to the list", async () => {
    render(<ChatPage />);
    fireEvent.click(await within(rail()).findByTestId("thread-archived-link"));
    await within(rail()).findByTestId("thread-archived-view");
    const menu = await openMenu("Old receipts");
    fireEvent.click(within(menu).getByTestId("thread-menu-unarchive"));
    await waitFor(() =>
      expect(H.posts.map((p) => p.path)).toContain("/chat/threads/t3/unarchive"),
    );
    await waitFor(() => expect(within(rail()).getByText("No archived chats.")).toBeTruthy());
    fireEvent.click(within(rail()).getByTestId("thread-archived-back"));
    expect(await within(rail()).findByText("Old receipts")).toBeTruthy();
    // Nothing archived any more: the link is gone.
    expect(within(rail()).queryByTestId("thread-archived-link")).toBeNull();
  });

  it("pressing an archived row brings it back AND opens it", async () => {
    render(<ChatPage />);
    fireEvent.click(await within(rail()).findByTestId("thread-archived-link"));
    await within(rail()).findByTestId("thread-archived-view");
    fireEvent.click(within(rail()).getByTestId("thread-row-t3"));
    expect(await screen.findByText("answer in Old receipts")).toBeTruthy();
    expect(H.posts.map((p) => p.path)).toContain("/chat/threads/t3/unarchive");
    expect(H.db.archived.has("t3")).toBe(false);
    await waitFor(() => expect(within(rail()).queryByTestId("thread-archived-view")).toBeNull());
  });

  it("Delete there is still two presses", async () => {
    render(<ChatPage />);
    fireEvent.click(await within(rail()).findByTestId("thread-archived-link"));
    await within(rail()).findByTestId("thread-archived-view");
    const menu = await openMenu("Old receipts");
    const del = within(menu).getByRole("menuitem", { name: /Delete chat/ });
    fireEvent.click(del);
    expect(H.dels).toEqual([]);
    fireEvent.click(del);
    await waitFor(() => expect(H.dels).toContain("/chat/threads/t3"));
    await waitFor(() => expect(within(rail()).getByText("No archived chats.")).toBeTruthy());
  });
});
