/**
 * v1.329.0 (calm chat W4 F8): the open chat's own ⋯ menu in the top bar can
 * rename, pin, archive and delete it.
 *
 * Before: those four lived only on the chat LIST row's ⋯, which on a phone
 * sits behind the "Chats" drawer. Now the top bar's ⋯ carries them for a
 * SAVED chat, through the same handlers the row menu uses:
 *  - a new, unsaved chat shows none of them (nothing to act on yet);
 *  - Rename turns the top bar's title into a box in place: Enter saves (one
 *    PUT, the list row's own rename shape), Escape leaves it as it was,
 *    focus goes back to the ⋯ button, and a failed save puts the old title
 *    back;
 *  - Pin to top / Unpin is the list's pin (the per-device `ij_chat_pinned`);
 *  - Archive goes through lib/archiveChat: idle = archived at once and the
 *    open chat is left for a new one; still working = the daemon's 409 opens
 *    ArchiveChatDialog with its list, and "Stop and archive" re-sends
 *    {stop: true};
 *  - Delete is two presses: the first arms it and says so (menu stays open,
 *    nothing deleted), the second deletes; closing the menu disarms it.
 *
 * Harness: the v1.328.0 archive harness (ChatPage rendered for real, a tiny
 * daemon behind the mocked get/put/del and the archive fetch).
 */

import { type ReactNode } from "react";
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
  type Row = { id: string; title: string; updated_at: string; messages: number; project_id?: string | null };
  return {
    FakeApiError,
    FakeStreamError,
    db: {
      rows: [] as Row[],
      archived: new Set<string>(),
      running: {} as Record<string, string[]>,
    },
    puts: [] as { path: string; body: Record<string, unknown> }[],
    dels: [] as string[],
    archives: [] as { id: string; body: Record<string, unknown> }[],
    /** Make the next title PUT fail (the daemon said no). */
    failTitlePut: false,
  };
});

const detail = (id: string) => {
  const r = H.db.rows.find((x) => x.id === id)!;
  return {
    id: r.id,
    title: r.title,
    updated_at: r.updated_at,
    messages: [
      { role: "user", content: `question in ${r.id}` },
      { role: "assistant", content: `answer in ${r.id}` },
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
  post: async () => ({}),
  put: async (path: string, body?: Record<string, unknown>) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    const b = (body ?? {}) as Record<string, unknown>;
    H.puts.push({ path, body: b });
    if (m && typeof b.title === "string") {
      if (H.failTitlePut) throw new H.FakeApiError("the title could not be saved", 500);
      const row = H.db.rows.find((r) => r.id === m[1]);
      if (row) row.title = b.title;
    }
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

const T1 = { id: "t1", title: "Quarterly notes", updated_at: "2026-10-08T10:00:00", messages: 2 };
const T2 = { id: "t2", title: "Harbor menu", updated_at: "2026-10-07T10:00:00", messages: 2 };

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
  H.db.archived.add(id);
  return Promise.resolve({
    status: 200,
    ok: true,
    statusText: "OK",
    json: async () => ({ id, archived: true, stopped: running, still_running: [] }),
  });
}

const trigger = () => screen.getByRole("button", { name: "More chat options" });

async function openMenu(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: "More chat options" }));
  return screen.findByRole("group", { name: "Chat options" });
}

const menuClosed = () => expect(screen.queryByRole("group", { name: "Chat options" })).toBeNull();

/** Open saved chat t1 and wait until its title is in the top bar. */
async function openSavedChat() {
  window.history.replaceState({}, "", "/chat?thread=t1");
  render(<ChatPage />);
  await screen.findByText("answer in t1");
  const top = await screen.findByTestId("chat-topbar");
  await waitFor(() => expect(within(top).getByTestId("chat-breadcrumb").textContent).toBe("Quarterly notes"));
  return top;
}

beforeEach(() => {
  __resetApiCache();
  H.db.rows = [{ ...T1 }, { ...T2 }];
  H.db.archived = new Set();
  H.db.running = {};
  H.puts.length = 0;
  H.dels.length = 0;
  H.archives.length = 0;
  H.failTitlePut = false;
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

describe("which chats get the actions", () => {
  it("a new, unsaved chat: the ⋯ menu has no Rename, Pin, Archive or Delete (its other controls stay)", async () => {
    render(<ChatPage />);
    await screen.findByRole("textbox", { name: "Message" });
    const panel = await openMenu();
    // CONTROL: the menu is there and holds what it always held.
    expect(within(panel).getByRole("button", { name: "Voice chat" })).toBeTruthy();
    expect(within(panel).getByRole("button", { name: "Show project panel" })).toBeTruthy();
    for (const id of ["chat-more-rename", "chat-more-pin", "chat-more-archive", "chat-more-delete"]) {
      expect(within(panel).queryByTestId(id)).toBeNull();
    }
  });

  it("a saved chat: Rename, Pin to top and Archive lead the menu and Delete chat ends it", async () => {
    await openSavedChat();
    const panel = await openMenu();
    const rows = within(panel)
      .getAllByRole("button")
      .map((b) => (b.textContent ?? "").trim())
      .filter(Boolean);
    // W5 G1 put the row menu's other three actions (Commit to memory, Turn
    // into workflow, Add to project) between Pin and Archive, in the row's
    // order; chat-topbar-actions-v1329 pins them.
    expect(rows.slice(0, 6)).toEqual([
      "Rename",
      "Pin to top",
      "Commit to memory",
      "Turn into workflow",
      "Add to project",
      "Archive",
    ]);
    expect(rows[rows.length - 1]).toBe("Delete chat");
    // The menu still holds its old controls between them.
    expect(within(panel).getByRole("button", { name: "Voice chat" })).toBeTruthy();
  });
});

describe("Rename", () => {
  it("turns the top bar's title into a box; Enter saves through the list's rename and focus returns to ⋯", async () => {
    const top = await openSavedChat();
    const panel = await openMenu();
    fireEvent.click(within(panel).getByTestId("chat-more-rename"));
    menuClosed();
    const box = (await within(top).findByRole("textbox", { name: "Rename this chat" })) as HTMLInputElement;
    expect(box.value).toBe("Quarterly notes");
    expect(document.activeElement).toBe(box);
    // ONE box: the list row of the same chat does not open its own.
    expect(screen.queryByRole("textbox", { name: "Rename chat" })).toBeNull();
    fireEvent.change(box, { target: { value: "  Q4 planning  " } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(within(top).getByTestId("chat-breadcrumb").textContent).toBe("Q4 planning"));
    await waitFor(() => expect(H.puts.filter((p) => "title" in p.body)).toHaveLength(1));
    const put = H.puts.find((p) => "title" in p.body)!;
    expect(put.path).toBe("/chat/threads/t1");
    expect(put.body.title).toBe("Q4 planning");
    // The list row's shape: the messages ride along, so nothing is lost.
    expect(Array.isArray(put.body.messages)).toBe(true);
    expect((put.body.messages as unknown[]).length).toBe(2);
    expect(document.activeElement).toBe(trigger());
    expect(within(top).queryByRole("textbox", { name: "Rename this chat" })).toBeNull();
    // The chat list says the new name too.
    await waitFor(() => expect(within(screen.getByTestId("chat-thread-rail")).getByText("Q4 planning")).toBeTruthy());
  });

  it("Escape leaves the title as it was and saves nothing", async () => {
    const top = await openSavedChat();
    fireEvent.click(within(await openMenu()).getByTestId("chat-more-rename"));
    const box = (await within(top).findByRole("textbox", { name: "Rename this chat" })) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "Something else" } });
    fireEvent.keyDown(box, { key: "Escape" });
    await waitFor(() => expect(within(top).getByTestId("chat-breadcrumb").textContent).toBe("Quarterly notes"));
    expect(H.puts.filter((p) => "title" in p.body)).toHaveLength(0);
    expect(document.activeElement).toBe(trigger());
  });

  it("clicking away saves, like the list row's rename", async () => {
    const top = await openSavedChat();
    fireEvent.click(within(await openMenu()).getByTestId("chat-more-rename"));
    const box = (await within(top).findByRole("textbox", { name: "Rename this chat" })) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "Harbor budget" } });
    fireEvent.blur(box);
    await waitFor(() => expect(within(top).getByTestId("chat-breadcrumb").textContent).toBe("Harbor budget"));
    await waitFor(() => expect(H.puts.filter((p) => p.body.title === "Harbor budget")).toHaveLength(1));
  });

  it("a save the daemon refuses puts the old title back", async () => {
    H.failTitlePut = true;
    const top = await openSavedChat();
    fireEvent.click(within(await openMenu()).getByTestId("chat-more-rename"));
    const box = (await within(top).findByRole("textbox", { name: "Rename this chat" })) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "Never saved" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.puts.filter((p) => p.body.title === "Never saved")).toHaveLength(1));
    await waitFor(() => expect(within(top).getByTestId("chat-breadcrumb").textContent).toBe("Quarterly notes"));
  });
});

describe("Pin", () => {
  it("pins the open chat (the list's per-device pin) and the menu then offers Unpin", async () => {
    await openSavedChat();
    const panel = await openMenu();
    const pin = within(panel).getByTestId("chat-more-pin");
    expect(pin.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(pin);
    menuClosed();
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem("ij_chat_pinned") ?? "[]")).toEqual(["t1"]));
    expect(document.activeElement).toBe(trigger());
    const again = within(await openMenu()).getByTestId("chat-more-pin");
    expect(again.textContent).toBe("Unpin");
    expect(again.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(again);
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem("ij_chat_pinned") ?? "[]")).toEqual([]));
  });
});

describe("Archive", () => {
  it("an idle chat is archived at once and the pane is left for a new chat", async () => {
    await openSavedChat();
    fireEvent.click(within(await openMenu()).getByTestId("chat-more-archive"));
    await waitFor(() => expect(screen.queryByText("answer in t1")).toBeNull());
    expect(H.archives).toEqual([{ id: "t1", body: {} }]);
    expect(H.db.archived.has("t1")).toBe(true);
    expect(screen.queryByTestId("archive-chat-dialog")).toBeNull();
  });

  it("a chat still working opens ArchiveChatDialog with the daemon's list; Stop and archive re-sends {stop: true}", async () => {
    H.db.running.t1 = ["a reply in progress"];
    await openSavedChat();
    fireEvent.click(within(await openMenu()).getByTestId("chat-more-archive"));
    const dialog = await screen.findByTestId("archive-chat-dialog");
    expect(within(dialog).getAllByTestId("archive-chat-running-item").map((li) => li.textContent)).toEqual([
      "a reply in progress",
    ]);
    expect(within(dialog).getByTestId("archive-chat-heading").textContent).toBe("Archive 'Quarterly notes'?");
    // Nothing archived yet: the chat is still on screen.
    expect(H.db.archived.has("t1")).toBe(false);
    expect(screen.getByText("answer in t1")).toBeTruthy();
    fireEvent.click(within(dialog).getByTestId("archive-chat-confirm"));
    await waitFor(() => expect(screen.queryByTestId("archive-chat-dialog")).toBeNull());
    expect(H.archives).toEqual([
      { id: "t1", body: {} },
      { id: "t1", body: { stop: true } },
    ]);
    await waitFor(() => expect(screen.queryByText("answer in t1")).toBeNull());
  });
});

describe("Delete", () => {
  it("the first press arms it and keeps the menu open; the second deletes the open chat", async () => {
    await openSavedChat();
    const panel = await openMenu();
    const del = within(panel).getByTestId("chat-more-delete");
    expect(del.textContent).toBe("Delete chat");
    fireEvent.click(del);
    await waitFor(() =>
      expect(within(panel).getByTestId("chat-more-delete").textContent).toBe("Delete for good? Press again"),
    );
    expect(within(panel).getByTestId("chat-more-delete").getAttribute("data-armed")).toBe("true");
    expect(screen.getByRole("group", { name: "Chat options" })).toBe(panel);
    expect(H.dels).toEqual([]);
    fireEvent.click(within(panel).getByTestId("chat-more-delete"));
    await waitFor(() => expect(H.dels).toEqual(["/chat/threads/t1"]));
    await waitFor(() => expect(screen.queryByText("answer in t1")).toBeNull());
    menuClosed();
  });

  it("closing the menu disarms it: the next visit starts at 'Delete chat' and one press deletes nothing", async () => {
    await openSavedChat();
    const panel = await openMenu();
    fireEvent.click(within(panel).getByTestId("chat-more-delete"));
    await waitFor(() =>
      expect(within(panel).getByTestId("chat-more-delete").textContent).toBe("Delete for good? Press again"),
    );
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    menuClosed();
    const again = within(await openMenu()).getByTestId("chat-more-delete");
    await waitFor(() => expect(again.textContent).toBe("Delete chat"));
    fireEvent.click(again);
    expect(H.dels).toEqual([]);
    expect(screen.getByText("answer in t1")).toBeTruthy();
  });
});
