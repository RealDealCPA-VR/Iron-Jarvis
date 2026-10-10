/**
 * v1.329.0 (calm chat W5 G1): the open chat's top-bar ⋯ offers every chat
 * action the chat list row's ⋯ offers.
 *
 * Before: the top bar had Rename / Pin / Archive / Delete (W4 F8) but not
 * Commit to memory, Turn into workflow and Add to project, which only the row
 * had (and on a phone the row sits behind the Chats drawer). Now the top bar
 * carries them for a SAVED chat, through the row's own handlers:
 *  - the chat's actions are ONE group (no hairline inside it), in the row's
 *    order, and Delete still ends the menu;
 *  - Commit to memory posts /chat/threads/{id}/remember and keeps the menu
 *    open with "Saved to memory" (its only feedback, as on the row);
 *  - Turn into workflow posts /crystallize, puts the draft card in the chat,
 *    closes the menu and hands focus back to ⋯;
 *  - Add to project opens an inline list (aria-expanded); a pick tags the chat
 *    (one PUT with project_id), closes the menu, and the PAGE follows the
 *    chat, so the next autosave keeps the new project (before, it sent the
 *    page's old project and put the chat straight back);
 *  - Remove from project is offered only for a chat in a project, and it keeps
 *    the chat's own saved folder (setup.workspace_dir): following the move
 *    must not reset the folder to the user's default, or the next autosave
 *    writes that over the chat's folder.
 *
 * Harness: the chat-more-menu-v1329 harness (ChatPage rendered for real, a
 * tiny daemon behind the mocked get/put/post).
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
  type Row = {
    id: string;
    title: string;
    updated_at: string;
    messages: number;
    project_id?: string | null;
    setup?: Record<string, unknown>;
  };
  return {
    FakeApiError,
    FakeStreamError,
    db: { rows: [] as Row[] },
    puts: [] as { path: string; body: Record<string, unknown> }[],
    posts: [] as string[],
    /** Hold the remember POST until the test lets it go. */
    rememberGate: null as null | Promise<void>,
  };
});

const PROJECTS = [
  { id: "p1", name: "Harbor books", root: null, root_exists: false },
  { id: "p2", name: "Q4 planning", root: null, root_exists: false },
];

const detail = (id: string) => {
  const r = H.db.rows.find((x) => x.id === id)!;
  return {
    id: r.id,
    title: r.title,
    updated_at: r.updated_at,
    project_id: r.project_id ?? null,
    ...(r.setup ? { setup: r.setup } : {}),
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
    if (path === "/chat/threads" || path.startsWith("/chat/threads?")) return { threads: H.db.rows };
    const m = /^\/chat\/threads\/([^/?]+)$/.exec(path);
    if (m && H.db.rows.some((r) => r.id === m[1])) return detail(m[1]);
    if (path === "/models") return { models: [] };
    if (path === "/projects") return { projects: PROJECTS };
    return {};
  },
  post: async (path: string) => {
    H.posts.push(path);
    if (path.endsWith("/remember")) {
      if (H.rememberGate) await H.rememberGate;
      return { ok: true, ref: "n1", source: "chat", distilled: false };
    }
    if (path.endsWith("/crystallize")) {
      return {
        name: "quarterly-notes",
        description: "Write the quarterly notes",
        steps: [{ id: "s1", name: "Draft", kind: "agent", agent: "builder", task: "Draft the notes" }],
      };
    }
    return {};
  },
  put: async (path: string, body?: Record<string, unknown>) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    const b = (body ?? {}) as Record<string, unknown>;
    H.puts.push({ path, body: b });
    const row = m ? H.db.rows.find((r) => r.id === m[1]) : undefined;
    if (row && "project_id" in b) row.project_id = (b.project_id as string | null) ?? null;
    return { id: m && m[1] !== "new" ? m[1] : "t-new", title: "t", updated_at: "2026-10-09T10:00:00" };
  },
  patch: async () => ({}),
  del: async () => undefined,
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
// The workflow draft card the crystallize step puts in the chat reads the router.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {}, back: () => {} }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/project/ProjectSurfaces", () => ({
  ProjectSurface: ({ view }: { view: string }) => <div data-testid="surface-stub">surface:{view}</div>,
}));

import ChatPage from "@/app/chat/page";
import { __resetApiCache } from "@/lib/apiCache";

const T1 = { id: "t1", title: "Quarterly notes", updated_at: "2026-10-08T10:00:00", messages: 2 };

const trigger = () => screen.getByRole("button", { name: "More chat options" });

async function openMenu(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: "More chat options" }));
  return screen.findByRole("group", { name: "Chat options" });
}

const menuOpen = () => screen.queryByRole("group", { name: "Chat options" });

async function openSavedChat() {
  window.history.replaceState({}, "", "/chat?thread=t1");
  render(<ChatPage />);
  await screen.findByText("answer in t1");
  const top = await screen.findByTestId("chat-topbar");
  // A chat in a project reads "<project>/Quarterly notes".
  await waitFor(() =>
    expect(within(top).getByTestId("chat-breadcrumb").textContent).toMatch(/(^|\/)Quarterly notes$/),
  );
  return top;
}

beforeEach(() => {
  __resetApiCache();
  H.db.rows = [{ ...T1 }];
  H.puts.length = 0;
  H.posts.length = 0;
  H.rememberGate = null;
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

describe("the top-bar ⋯ offers the row ⋯'s chat actions", () => {
  it("a saved chat: one group in the row's order, then the menu's own controls, Delete last", async () => {
    await openSavedChat();
    const panel = await openMenu();
    const rows = within(panel)
      .getAllByRole("button")
      .map((b) => (b.textContent ?? "").trim())
      .filter(Boolean);
    expect(rows.slice(0, 6)).toEqual([
      "Rename",
      "Pin to top",
      "Commit to memory",
      "Turn into workflow",
      "Add to project",
      "Archive",
    ]);
    expect(rows[rows.length - 1]).toBe("Delete chat");
    // ONE hairline group: no divider between Rename and Archive.
    const rename = within(panel).getByTestId("chat-more-rename");
    const archive = within(panel).getByTestId("chat-more-archive");
    let n: Element | null = rename.nextElementSibling;
    const between: Element[] = [];
    while (n && n !== archive) {
      between.push(n);
      n = n.nextElementSibling;
    }
    expect(n).toBe(archive);
    expect(between.some((el) => el.className.includes("h-px"))).toBe(false);
    // A divider follows the group.
    expect(archive.nextElementSibling?.className).toContain("h-px");
  });

  it("a new, unsaved chat shows none of the three", async () => {
    render(<ChatPage />);
    await screen.findByRole("textbox", { name: "Message" });
    const panel = await openMenu();
    expect(within(panel).getByRole("button", { name: "Voice chat" })).toBeTruthy();
    for (const id of ["chat-more-remember", "chat-more-workflow", "chat-more-project"]) {
      expect(within(panel).queryByTestId(id)).toBeNull();
    }
  });
});

describe("Commit to memory", () => {
  it("posts the row's remember, keeps the menu open and says Saved to memory", async () => {
    let release = () => {};
    H.rememberGate = new Promise<void>((r) => (release = r));
    await openSavedChat();
    const panel = await openMenu();
    const btn = within(panel).getByTestId("chat-more-remember");
    btn.focus();
    fireEvent.click(btn);
    await waitFor(() => expect(H.posts).toEqual(["/chat/threads/t1/remember"]));
    // Working: it says so without dropping focus, and a second press posts nothing.
    await waitFor(() => expect(within(panel).getByTestId("chat-more-remember").getAttribute("aria-disabled")).toBe("true"));
    expect(document.activeElement).toBe(within(panel).getByTestId("chat-more-remember"));
    fireEvent.click(within(panel).getByTestId("chat-more-remember"));
    release();
    await waitFor(() => expect(within(panel).getByTestId("chat-more-remember").textContent).toBe("Saved to memory"));
    expect(H.posts).toEqual(["/chat/threads/t1/remember"]);
    expect(menuOpen()).toBe(panel);
  });
});

describe("Turn into workflow", () => {
  it("posts the row's crystallize, puts the draft in the chat, closes the menu and focus goes back to ⋯", async () => {
    await openSavedChat();
    const panel = await openMenu();
    const btn = within(panel).getByTestId("chat-more-workflow");
    btn.focus();
    fireEvent.click(btn);
    await waitFor(() => expect(H.posts).toContain("/chat/threads/t1/crystallize"));
    await screen.findByText("Here's this conversation as a reusable workflow:");
    await waitFor(() => expect(menuOpen()).toBeNull());
    expect(document.activeElement).toBe(trigger());
  });
});

describe("Add to project", () => {
  it("opens an inline list; a pick tags the open chat, closes the menu, and the next save keeps the project", async () => {
    await openSavedChat();
    const panel = await openMenu();
    const head = within(panel).getByTestId("chat-more-project");
    expect(head.getAttribute("aria-expanded")).toBe("false");
    expect(within(panel).queryByTestId("chat-more-project-list")).toBeNull();
    fireEvent.click(head);
    const list = await within(panel).findByTestId("chat-more-project-list");
    expect(head.getAttribute("aria-expanded")).toBe("true");
    // Not in a project yet: nothing to remove.
    expect(within(list).queryByTestId("chat-more-project-remove")).toBeNull();
    const pick = within(list).getByRole("button", { name: "Q4 planning" });
    expect(pick.getAttribute("aria-pressed")).toBe("false");
    pick.focus();
    fireEvent.click(pick);
    await waitFor(() =>
      expect(H.puts.some((p) => p.path === "/chat/threads/t1" && p.body.project_id === "p2")).toBe(true),
    );
    // The row's shape: the messages ride along.
    const tag = H.puts.find((p) => p.body.project_id === "p2")!;
    expect(Array.isArray(tag.body.messages)).toBe(true);
    await waitFor(() => expect(menuOpen()).toBeNull());
    expect(document.activeElement).toBe(trigger());

    // The page followed the chat: the next autosave keeps p2.
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "and the totals?" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await screen.findByText("Streamed answer.");
    await waitFor(() => {
      const saves = H.puts.filter(
        (p) =>
          p.path === "/chat/threads/t1" &&
          JSON.stringify(p.body.messages ?? []).includes("Streamed answer."),
      );
      expect(saves.length).toBeGreaterThan(0);
      expect(saves[saves.length - 1].body.project_id).toBe("p2");
    });
    expect(H.db.rows[0].project_id).toBe("p2");

    // Reopened, the menu marks p2 and offers Remove from project.
    const again = await openMenu();
    fireEvent.click(within(again).getByTestId("chat-more-project"));
    const list2 = await within(again).findByTestId("chat-more-project-list");
    expect(within(list2).getByRole("button", { name: "Q4 planning" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(within(list2).getByTestId("chat-more-project-remove"));
    await waitFor(() =>
      expect(H.puts.some((p) => p.path === "/chat/threads/t1" && p.body.project_id === null)).toBe(true),
    );
    await waitFor(() => expect(H.db.rows[0].project_id).toBeNull());
  });

  it("each visit opens with the project list folded", async () => {
    await openSavedChat();
    const panel = await openMenu();
    fireEvent.click(within(panel).getByTestId("chat-more-project"));
    await within(panel).findByTestId("chat-more-project-list");
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    await waitFor(() => expect(menuOpen()).toBeNull());
    const again = await openMenu();
    expect(within(again).getByTestId("chat-more-project").getAttribute("aria-expanded")).toBe("false");
    expect(within(again).queryByTestId("chat-more-project-list")).toBeNull();
  });

  it("Remove from project keeps the chat's own folder: the next save still carries it", async () => {
    // The user's default folder differs, so a reset to it is visible.
    H.db.rows = [{ ...T1, project_id: "p1", setup: { workspace_dir: "C:/work/harbor-custom" } }];
    window.localStorage.setItem("ij_chat_workspace", "C:/work/my-default");
    await openSavedChat();
    const panel = await openMenu();
    fireEvent.click(within(panel).getByTestId("chat-more-project"));
    const list = await within(panel).findByTestId("chat-more-project-list");
    expect(within(list).getByRole("button", { name: "Harbor books" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(within(list).getByTestId("chat-more-project-remove"));
    await waitFor(() => expect(H.db.rows[0].project_id).toBeNull());
    await waitFor(() => expect(menuOpen()).toBeNull());

    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "and the totals?" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await screen.findByText("Streamed answer.");
    await waitFor(() => {
      const saves = H.puts.filter(
        (p) =>
          p.path === "/chat/threads/t1" &&
          JSON.stringify(p.body.messages ?? []).includes("Streamed answer."),
      );
      expect(saves.length).toBeGreaterThan(0);
      const last = saves[saves.length - 1].body;
      expect(last.project_id).toBeNull();
      expect((last.setup as { workspace_dir?: string } | undefined)?.workspace_dir).toBe("C:/work/harbor-custom");
    });
    // No save after the move wrote the default (or an empty) folder.
    const moved = H.puts.findIndex((p) => p.path === "/chat/threads/t1" && p.body.project_id === null);
    for (const p of H.puts.slice(moved + 1)) {
      const setup = p.body.setup as { workspace_dir?: string } | undefined;
      if (setup && "workspace_dir" in setup) expect(setup.workspace_dir).toBe("C:/work/harbor-custom");
    }
  });
});
