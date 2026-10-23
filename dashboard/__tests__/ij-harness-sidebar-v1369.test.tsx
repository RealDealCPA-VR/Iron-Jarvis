/**
 * v1.369.0 (IJ harness Phase 6) — the IJ harness view's left pane and the
 * view around it (components/harness/HarnessSidebar, IjHarnessView).
 *
 *  - sessions grouped by folder, each group named by its DISPLAY name; the
 *    rename sets the display name (POST /build/folders/rename); a project
 *    root says it renames the PROJECT; "+" starts a session in that folder;
 *  - New session posts the folder; a refused folder shows the daemon's words;
 *  - the Terminals group lists the live terminals with their state dots and
 *    opens one through the page's own path;
 *  - the open session is the stable address (`?session=<id>`), and the list
 *    is read again on a timer only while the window is visible.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    posts: [] as [string, unknown][],
    postFail: {} as Record<string, Error>,
    getCount: {} as Record<string, number>,
  };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.getCount[p] = (api.getCount[p] ?? 0) + 1;
    return p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
  },
  post: (p: string, b?: unknown) => {
    api.posts.push([p, b]);
    if (api.postFail[p]) return Promise.reject(api.postFail[p]);
    if (p === "/build/sessions") return Promise.resolve({ id: "new1", title: "New session", folder: (b as { folder: string }).folder });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: ({ onSelect }: { onSelect: (p: string) => void }) => (
    <button type="button" data-testid="tree-pick" onClick={() => onSelect("C:\\picked")}>
      pick
    </button>
  ),
}));
// The session itself has its own file; here it is a marker.
vi.mock("@/components/harness/HarnessSession", () => ({
  HarnessSession: ({ threadId, listRunning }: { threadId: string; listRunning: boolean }) => (
    <div data-testid="session-stub" data-id={threadId} data-running={String(listRunning)} />
  ),
}));

import { HarnessSidebar } from "@/components/harness/HarnessSidebar";
import { IjHarnessView, SESSION_LIST_POLL_MS } from "@/components/harness/IjHarnessView";
import { decodeFolders } from "@/lib/buildSessions";
import type { RailPane } from "@/components/terminal/PaneRail";

const LIST = {
  folders: [
    {
      path: "C:\\work\\shop",
      name: "Shop site",
      is_project: true,
      project_id: "p1",
      sessions: [
        { id: "a1", title: "Add a cart", updated_at: "2026-10-22T10:00:00", running: true, waiting: false, archived: false },
        { id: "a2", title: "Fix the footer", updated_at: "2026-10-21T10:00:00", running: false, waiting: true, archived: false },
      ],
    },
    {
      path: "C:\\work\\tools",
      name: "tools",
      is_project: false,
      project_id: null,
      sessions: [{ id: "b1", title: "Script", updated_at: "2026-10-20T10:00:00", running: false, waiting: false, archived: false }],
    },
  ],
};

const panes: RailPane[] = [
  { id: "t1", label: "builder", state: "blocked", cwd: "C:\\work\\shop" },
  { id: "t2", label: "pwsh", state: "shell", cwd: "C:\\work" },
];

beforeEach(() => {
  api.gets = { "/build/sessions": LIST };
  api.posts = [];
  api.postFail = {};
  api.getCount = {};
  localStorage.clear();
  window.history.replaceState(null, "", "/terminals");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function sidebar(over: Partial<React.ComponentProps<typeof HarnessSidebar>> = {}) {
  const props = {
    folders: decodeFolders(LIST),
    listError: null,
    selectedId: "a1",
    onSelect: vi.fn(),
    onStart: vi.fn(async () => null as string | null),
    onRename: vi.fn(async () => null as string | null),
    defaultFolder: "C:\\work\\shop",
    terminals: panes,
    onOpenTerminal: vi.fn(),
    ...over,
  };
  render(<HarnessSidebar {...props} />);
  return props;
}

describe("sessions grouped by folder", () => {
  it("names each group by its display name and lists its sessions newest first, with their state", () => {
    sidebar();
    const groups = screen.getAllByTestId("harness-folder-group");
    expect(groups.map((g) => within(g).getByTestId("harness-folder-name").textContent)).toEqual(["Shop site", "tools"]);
    expect(within(groups[0]).getByTestId("harness-session-row-a1")).toHaveTextContent("working");
    expect(within(groups[0]).getByTestId("harness-session-row-a2")).toHaveTextContent("needs you");
    expect(within(groups[0]).getByTestId("harness-session-row-a1")).toHaveAttribute("aria-current", "true");
  });

  it("rename sets the display name (a project root says it renames the project)", async () => {
    const p = sidebar();
    const group = screen.getAllByTestId("harness-folder-group")[0];
    const btn = within(group).getByTestId("harness-folder-rename");
    expect(btn).toHaveAttribute("aria-label", "Rename the project Shop site");
    fireEvent.click(btn);
    const input = within(group).getByTestId("harness-folder-rename-input");
    fireEvent.change(input, { target: { value: "Shop" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(p.onRename).toHaveBeenCalledWith("C:\\work\\shop", "Shop"));
    // A plain folder says it renames how the folder shows in Build.
    expect(
      within(screen.getAllByTestId("harness-folder-group")[1]).getByTestId("harness-folder-rename"),
    ).toHaveAttribute("aria-label", "Rename tools in Build");
  });

  it("each group's + starts a session in that folder", async () => {
    const p = sidebar();
    fireEvent.click(within(screen.getAllByTestId("harness-folder-group")[1]).getByTestId("harness-folder-new"));
    await waitFor(() => expect(p.onStart).toHaveBeenCalledWith("C:\\work\\tools"));
  });

  it("New session starts in the folder typed or picked, and shows a refusal in the daemon's words", async () => {
    const onStart = vi.fn(async (f: string) => (f === "C:\\Windows" ? "This folder cannot be used: it is protected" : null));
    sidebar({ onStart });
    fireEvent.click(screen.getByTestId("harness-new-session"));
    const input = screen.getByTestId("harness-new-folder") as HTMLInputElement;
    expect(input.value).toBe("C:\\work\\shop");
    fireEvent.change(input, { target: { value: "C:\\Windows" } });
    fireEvent.click(screen.getByTestId("harness-new-start"));
    expect(await screen.findByRole("alert")).toHaveTextContent("This folder cannot be used: it is protected");
    fireEvent.click(screen.getByRole("button", { name: "Choose a folder" }));
    fireEvent.click(screen.getByTestId("tree-pick"));
    expect(input.value).toBe("C:\\picked");
    fireEvent.click(screen.getByTestId("harness-new-start"));
    await waitFor(() => expect(onStart).toHaveBeenLastCalledWith("C:\\picked"));
    await waitFor(() => expect(screen.queryByTestId("harness-new-session-form")).toBeNull());
  });
});

describe("the Terminals group", () => {
  it("lists the live terminals with the rail's state dots, and opens one through the page's path", () => {
    const p = sidebar();
    const group = screen.getByTestId("harness-terminals");
    expect(within(group).getByTestId("harness-terminal-row-t1")).toHaveTextContent("builder");
    expect(within(group).getByTestId("pane-dot-blocked")).toBeInTheDocument();
    expect(within(group).getByTestId("pane-dot-shell")).toBeInTheDocument();
    fireEvent.click(within(group).getByTestId("harness-terminal-row-t1"));
    expect(p.onOpenTerminal).toHaveBeenCalledWith("t1");
  });

  it("says so when none is open", () => {
    sidebar({ terminals: [] });
    expect(screen.getByTestId("harness-terminals")).toHaveTextContent("No terminals open.");
  });
});

describe("the view", () => {
  const terminal = { find: () => null, open: async () => null, render: () => null, close: () => {} };

  it("opens the session the address names, and puts the open session in the address", async () => {
    render(<IjHarnessView initialSession="b1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    // The address is written by an effect after the commit that shows the
    // session: wait for the address itself (v1.369.1).
    await waitFor(() => {
      expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "b1");
      expect(new URL(window.location.href).searchParams.get("session")).toBe("b1");
    });
    fireEvent.click(screen.getByTestId("harness-session-row-a2"));
    await waitFor(() => {
      expect(new URL(window.location.href).searchParams.get("session")).toBe("a2");
      expect(localStorage.getItem("ij.build.session")).toBe("a2");
    });
  });

  it("with no address, opens the session this window had open (while it is listed), and tells the session its turn runs", async () => {
    localStorage.setItem("ij.build.session", "a1");
    render(<IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "a1"));
    expect(screen.getByTestId("session-stub")).toHaveAttribute("data-running", "true");
  });

  it("with no address, the remembered session opens even when it is NOT the newest (v1.369.0 review)", async () => {
    // The first render's "nothing picked yet" used to be written back before
    // the list answered, erasing the remembered session: Build then opened
    // the newest session instead of the one it had open.
    localStorage.setItem("ij.build.session", "b1");
    render(<IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    // The remembered key and the address are written by an EFFECT after the
    // commit that shows the session: wait for THEM, not for the screen (the
    // v1.369.0 Tests gate read the address before the effect ran: null).
    await waitFor(() => {
      expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "b1");
      expect(localStorage.getItem("ij.build.session")).toBe("b1");
      expect(new URL(window.location.href).searchParams.get("session")).toBe("b1");
    });
  });

  it("a session link pressed while the view shows another session opens it (v1.369.0 review)", async () => {
    render(<IjHarnessView initialSession="a1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "a1"));
    act(() => {
      window.dispatchEvent(new CustomEvent("ij:open-build-session", { detail: { id: "b1" } }));
    });
    await waitFor(() => {
      expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "b1");
      expect(new URL(window.location.href).searchParams.get("session")).toBe("b1");
    });
  });

  it("a new session from the left pane is created and opened", async () => {
    render(<IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={"C:\\work"} terminal={terminal} />);
    await screen.findByTestId("session-stub");
    fireEvent.click(screen.getByTestId("harness-new-session"));
    fireEvent.click(screen.getByTestId("harness-new-start"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "new1"));
    // The folder only: the daemon chooses the account (its Default, even at
    // its limit — v1.368.0); the screen never picks one for a new session.
    // (v1.378.0, moved on purpose: the folder is the session in front's —
    // a1 in C:\work\shop — not the Terminals view's C:\work;
    // ij-harness-new-folder-v1378.test.tsx.)
    expect(api.posts).toContainEqual(["/build/sessions", { folder: "C:\\work\\shop" }]);
  });

  it("reads the list again on a timer while the window is visible, and not while it is hidden", async () => {
    const setSpy = vi.spyOn(window, "setInterval");
    const clearSpy = vi.spyOn(window, "clearInterval");
    render(<IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    await waitFor(() => expect(api.getCount["/build/sessions"]).toBe(1));
    const poll = setSpy.mock.calls.find((c) => c[1] === SESSION_LIST_POLL_MS);
    expect(poll).toBeTruthy();
    const pollId = setSpy.mock.results[setSpy.mock.calls.indexOf(poll!)].value;
    await act(async () => {
      (poll![0] as () => void)();
    });
    await waitFor(() => expect(api.getCount["/build/sessions"]).toBe(2));
    // Hidden: the timer stops, and no new one starts.
    setSpy.mockClear();
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(clearSpy).toHaveBeenCalledWith(pollId);
    expect(setSpy.mock.calls.filter((c) => c[1] === SESSION_LIST_POLL_MS)).toHaveLength(0);
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    setSpy.mockRestore();
    clearSpy.mockRestore();
  });

  it("an Iron Jarvis without Build sessions says so", async () => {
    delete api.gets["/build/sessions"];
    render(<IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("This version of Iron Jarvis has no Build sessions yet.");
  });
});
